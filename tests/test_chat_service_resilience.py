from app.config import Settings
from app.models import ChatMessageRequest, FoundryReply, UserContext
from app.policy_router import PolicyRouter
from app.services.chat_service import ChatService
from app.services.cosmos_store import ChatStore


class FailingStore(ChatStore):
    async def create_session(self, user_id: str, session_id: str | None = None):
        raise RuntimeError("store unavailable")

    async def get_messages(self, session_id: str, user_id: str):
        raise RuntimeError("store unavailable")

    async def save_message(self, message):
        raise RuntimeError("store unavailable")


class StubFoundryClient:
    async def ask_workflow(self, **kwargs):
        return FoundryReply(text="Card replacement is 1-2 business days expedited.", raw={"mode": "stub"})


async def test_create_session_falls_back_when_store_fails() -> None:
    service = ChatService(store=FailingStore(), router=PolicyRouter(Settings()), foundry_client=StubFoundryClient())
    user = UserContext(user_id="user@example.com")

    session = await service.create_session(user=user, session_id="session-123")

    assert session.id == "session-123"
    assert session.user_id == "user@example.com"


async def test_get_history_returns_empty_when_store_fails() -> None:
    service = ChatService(store=FailingStore(), router=PolicyRouter(Settings()), foundry_client=StubFoundryClient())
    user = UserContext(user_id="user@example.com")

    history = await service.get_history(session_id="session-123", user=user)

    assert history == []


async def test_send_message_succeeds_when_store_fails() -> None:
    service = ChatService(store=FailingStore(), router=PolicyRouter(Settings()), foundry_client=StubFoundryClient())
    user = UserContext(user_id="user@example.com")

    response = await service.send_message(
        request=ChatMessageRequest(session_id="session-123", message="My card was declined"),
        user=user,
        request_id="req-123",
    )

    assert response.session_id == "session-123"
    assert response.domain == "card_services"
    assert response.assistant_response
