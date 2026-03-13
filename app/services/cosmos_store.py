from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from uuid import uuid4

from azure.cosmos.aio import CosmosClient
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError
from azure.identity.aio import DefaultAzureCredential

from ..config import Settings
from ..models import ChatMessage, ChatSession


class ChatStore(ABC):
    @abstractmethod
    async def create_session(self, user_id: str, session_id: str | None = None) -> ChatSession:
        raise NotImplementedError

    @abstractmethod
    async def get_messages(self, session_id: str, user_id: str) -> list[ChatMessage]:
        raise NotImplementedError

    @abstractmethod
    async def save_message(self, message: ChatMessage) -> None:
        raise NotImplementedError


class InMemoryChatStore(ChatStore):
    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}
        self._messages: dict[str, list[ChatMessage]] = {}

    async def create_session(self, user_id: str, session_id: str | None = None) -> ChatSession:
        resolved_id = session_id or str(uuid4())
        session = ChatSession(id=resolved_id, user_id=user_id)
        self._sessions[resolved_id] = session
        self._messages.setdefault(resolved_id, [])
        return session

    async def get_messages(self, session_id: str, user_id: str) -> list[ChatMessage]:
        session = self._sessions.get(session_id)
        if not session:
            return []
        if session.user_id != user_id:
            return []
        return sorted(self._messages.get(session_id, []), key=lambda item: item.created_at)

    async def save_message(self, message: ChatMessage) -> None:
        self._messages.setdefault(message.session_id, []).append(message)


class CosmosChatStore(ChatStore):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: CosmosClient | None = None
        self._credential: DefaultAzureCredential | None = None
        self._database = None
        self._sessions_container = None
        self._messages_container = None

    async def startup(self) -> None:
        if not self._settings.cosmos_endpoint:
            raise ValueError("COSMOS_ENDPOINT is required when cosmos is enabled.")

        if self._settings.cosmos_key:
            self._client = CosmosClient(self._settings.cosmos_endpoint, credential=self._settings.cosmos_key)
        else:
            self._credential = DefaultAzureCredential()
            self._client = CosmosClient(self._settings.cosmos_endpoint, credential=self._credential)

        self._database = self._client.get_database_client(self._settings.cosmos_database)
        self._sessions_container = self._database.get_container_client(self._settings.cosmos_sessions_container)
        self._messages_container = self._database.get_container_client(self._settings.cosmos_messages_container)

    async def shutdown(self) -> None:
        if self._client:
            await self._client.close()
        if self._credential:
            await self._credential.close()

    async def create_session(self, user_id: str, session_id: str | None = None) -> ChatSession:
        assert self._sessions_container is not None
        resolved_id = session_id or str(uuid4())
        now = datetime.utcnow()
        item = {
            "id": resolved_id,
            "userId": user_id,
            "createdAt": now.isoformat(),
            "updatedAt": now.isoformat(),
            "type": "session",
        }
        try:
            await self._sessions_container.upsert_item(item)
        except CosmosHttpResponseError as exc:
            raise RuntimeError(f"Failed to create chat session in Cosmos DB: {exc}") from exc

        return ChatSession(id=resolved_id, user_id=user_id, created_at=now, updated_at=now)

    async def get_messages(self, session_id: str, user_id: str) -> list[ChatMessage]:
        assert self._messages_container is not None
        query = (
            "SELECT * FROM c WHERE c.sessionId = @sessionId AND c.userId = @userId "
            "ORDER BY c.createdAt ASC"
        )
        params = [
            {"name": "@sessionId", "value": session_id},
            {"name": "@userId", "value": user_id},
        ]

        results: list[ChatMessage] = []
        try:
            iterator = self._messages_container.query_items(
                query=query,
                parameters=params,
                partition_key=session_id,
            )
            async for item in iterator:
                results.append(
                    ChatMessage(
                        id=item["id"],
                        session_id=item["sessionId"],
                        user_id=item["userId"],
                        role=item["role"],
                        content=item["content"],
                        domain=item.get("domain", "unknown"),
                        created_at=datetime.fromisoformat(item["createdAt"]),
                    )
                )
        except CosmosResourceNotFoundError:
            return []
        except CosmosHttpResponseError as exc:
            raise RuntimeError(f"Failed to read chat history from Cosmos DB: {exc}") from exc

        return results

    async def save_message(self, message: ChatMessage) -> None:
        assert self._messages_container is not None
        item = {
            "id": message.id,
            "sessionId": message.session_id,
            "userId": message.user_id,
            "role": message.role,
            "content": message.content,
            "domain": message.domain,
            "createdAt": message.created_at.isoformat(),
            "type": "message",
        }
        try:
            await self._messages_container.create_item(item)
        except CosmosHttpResponseError as exc:
            raise RuntimeError(f"Failed to write chat message to Cosmos DB: {exc}") from exc


async def build_chat_store(settings: Settings) -> ChatStore:
    if settings.cosmos_enabled:
        cosmos_store = CosmosChatStore(settings)
        await cosmos_store.startup()
        return cosmos_store

    return InMemoryChatStore()
