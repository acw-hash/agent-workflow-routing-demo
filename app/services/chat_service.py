from __future__ import annotations

from datetime import datetime
import logging
from uuid import uuid4

from ..models import ChatMessage, ChatMessageRequest, ChatMessageResponse, ChatSession, UserContext
from ..policy_router import PolicyRouter
from .cosmos_store import ChatStore
from .foundry_client import FoundryWorkflowClient

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(self, store: ChatStore, router: PolicyRouter, foundry_client: FoundryWorkflowClient) -> None:
        self._store = store
        self._router = router
        self._foundry_client = foundry_client

    async def create_session(self, user: UserContext, session_id: str | None = None) -> ChatSession:
        try:
            return await self._store.create_session(user.user_id, session_id)
        except Exception as exc:  # pylint: disable=broad-except
            fallback_id = session_id or str(uuid4())
            now = datetime.utcnow()
            logger.warning(
                "Chat session persistence failed for user %s; using transient session %s. Error: %s",
                user.user_id,
                fallback_id,
                exc,
            )
            return ChatSession(id=fallback_id, user_id=user.user_id, created_at=now, updated_at=now)

    async def get_history(self, session_id: str, user: UserContext) -> list[ChatMessage]:
        try:
            return await self._store.get_messages(session_id=session_id, user_id=user.user_id)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "Chat history read failed for session %s; continuing without history. Error: %s",
                session_id,
                exc,
            )
            return []

    async def send_message(self, request: ChatMessageRequest, user: UserContext, request_id: str) -> ChatMessageResponse:
        now = datetime.utcnow()

        session = await self.create_session(user, request.session_id)

        decision = self._router.route(request.message)
        policy_context = self._router.get_policy_context(decision.domain)
        logger.info(
            "Chat request routed request_id=%s session_id=%s user_id=%s domain=%s policy_source=%s reason=%s",
            request_id,
            session.id,
            user.user_id,
            decision.domain,
            decision.policy_source,
            decision.reason,
        )

        history = await self.get_history(session.id, user)
        foundry_history = [
            {"role": item.role, "content": item.content, "created_at": item.created_at.isoformat()}
            for item in history[-20:]
        ]

        user_message = ChatMessage(
            session_id=session.id,
            user_id=user.user_id,
            role="user",
            content=request.message,
            domain=decision.domain,
            created_at=now,
        )
        try:
            await self._store.save_message(user_message)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "Failed to persist user message for session %s (request %s). Error: %s",
                session.id,
                request_id,
                exc,
            )

        try:
            foundry_reply = await self._foundry_client.ask_workflow(
                user_query=request.message,
                session_id=session.id,
                user_id=user.user_id,
                domain=decision.domain,
                policy_context=policy_context,
                history=foundry_history,
                request_id=request_id,
            )
            foundry_mode = foundry_reply.raw.get("mode", "workflow-direct")
            logger.info(
                "Foundry invocation completed request_id=%s session_id=%s domain=%s mode=%s route_label=%s selected_agent=%s",
                request_id,
                session.id,
                decision.domain,
                foundry_mode,
                foundry_reply.raw.get("route_label"),
                foundry_reply.raw.get("selected_agent"),
            )
            assistant_text = foundry_reply.text
            if self._looks_like_non_answer(assistant_text):
                logger.warning(
                    "Foundry returned non-answer; using grounded fallback request_id=%s session_id=%s domain=%s mode=%s",
                    request_id,
                    session.id,
                    decision.domain,
                    foundry_mode,
                )
                assistant_text = self._router.grounded_response(decision, request.message)
        except Exception as exc:
            logger.exception(
                "Foundry invocation failed request_id=%s session_id=%s domain=%s error=%s",
                request_id,
                session.id,
                decision.domain,
                exc,
            )
            assistant_text = self._router.grounded_response(decision, request.message)

        message_id = str(uuid4())
        assistant_message = ChatMessage(
            id=message_id,
            session_id=session.id,
            user_id=user.user_id,
            role="assistant",
            content=assistant_text,
            domain=decision.domain,
            created_at=datetime.utcnow(),
        )
        try:
            await self._store.save_message(assistant_message)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "Failed to persist assistant message for session %s (request %s). Error: %s",
                session.id,
                request_id,
                exc,
            )

        return ChatMessageResponse(
            session_id=session.id,
            message_id=message_id,
            domain=decision.domain,
            assistant_response=assistant_text,
            policy_source=decision.policy_source,
            created_at=assistant_message.created_at,
        )

    @staticmethod
    def _looks_like_non_answer(text: str) -> bool:
        lowered = text.lower().strip()
        non_answer_signals = [
            "i don't have that information",
            "i do not have that information",
            "based on current policy documents",
            "cannot help with that",
        ]
        return any(signal in lowered for signal in non_answer_signals)
