from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .auth import get_user_context
from .config import Settings, get_settings
from .models import ChatHistoryResponse, ChatMessageRequest, ChatMessageResponse, SessionCreateRequest, SessionCreateResponse, UserContext
from .policy_router import PolicyRouter
from .services.chat_service import ChatService
from .services.cosmos_store import CosmosChatStore, build_chat_store
from .services.foundry_client import FoundryWorkflowClient

logger = logging.getLogger(__name__)


def _parse_origins(csv_origins: str) -> list[str]:
    if csv_origins.strip() == "*":
        return ["*"]
    return [origin.strip() for origin in csv_origins.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    logging.basicConfig(level=settings.log_level)
    store = await build_chat_store(settings)
    foundry_client = FoundryWorkflowClient(settings)
    router = PolicyRouter(settings)
    chat_service = ChatService(store=store, router=router, foundry_client=foundry_client)

    app.state.settings = settings
    app.state.store = store
    app.state.foundry_client = foundry_client
    app.state.chat_service = chat_service

    logger.info("Application startup complete.")

    try:
        yield
    finally:
        if isinstance(store, CosmosChatStore):
            await store.shutdown()
        await foundry_client.close()


app = FastAPI(title="Policy Chatbot API", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id", str(uuid4()))
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response


settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_origins(settings.cors_allowed_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config/public")
async def public_config(current_settings: Settings = Depends(get_settings)) -> dict[str, str | bool]:
    return {
        "allowAnonymous": current_settings.allow_anonymous,
        "tenantId": current_settings.entra_tenant_id,
        "clientId": current_settings.entra_client_id,
        "audience": current_settings.entra_audience,
    }


@app.post("/api/chat/session", response_model=SessionCreateResponse)
async def create_session(
    request_model: SessionCreateRequest,
    request: Request,
    user: UserContext = Depends(get_user_context),
) -> SessionCreateResponse:
    chat_service: ChatService = request.app.state.chat_service
    session = await chat_service.create_session(user, session_id=request_model.session_id)
    return SessionCreateResponse(session_id=session.id, created_at=session.created_at)


@app.get("/api/chat/session/{session_id}", response_model=ChatHistoryResponse)
async def get_history(
    session_id: str,
    request: Request,
    user: UserContext = Depends(get_user_context),
) -> ChatHistoryResponse:
    chat_service: ChatService = request.app.state.chat_service
    messages = await chat_service.get_history(session_id=session_id, user=user)
    if not messages:
        raise HTTPException(status_code=404, detail="Session not found or no history available.")
    return ChatHistoryResponse(session_id=session_id, user_id=user.user_id, messages=messages)


@app.post("/api/chat/message", response_model=ChatMessageResponse)
async def send_message(
    body: ChatMessageRequest,
    request: Request,
    user: UserContext = Depends(get_user_context),
) -> ChatMessageResponse:
    chat_service: ChatService = request.app.state.chat_service
    request_id = getattr(request.state, "request_id", str(uuid4()))
    return await chat_service.send_message(request=body, user=user, request_id=request_id)
