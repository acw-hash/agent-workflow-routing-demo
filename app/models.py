from datetime import datetime
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    session_id: Optional[str] = None


class SessionCreateResponse(BaseModel):
    session_id: str
    created_at: datetime


class ChatMessageRequest(BaseModel):
    session_id: Optional[str] = None
    message: str = Field(min_length=1, max_length=4000)


class ChatMessageResponse(BaseModel):
    session_id: str
    message_id: str
    domain: Literal["card_services", "fraud", "refunds_disputes", "unknown"]
    assistant_response: str
    policy_source: Optional[str] = None
    created_at: datetime


class ChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    user_id: str
    role: Literal["user", "assistant"]
    content: str
    domain: Literal["card_services", "fraud", "refunds_disputes", "unknown"]
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChatSession(BaseModel):
    id: str
    user_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ChatHistoryResponse(BaseModel):
    session_id: str
    user_id: str
    messages: list[ChatMessage]


class UserContext(BaseModel):
    user_id: str
    tenant_id: Optional[str] = None
    display_name: Optional[str] = None


class FoundryReply(BaseModel):
    text: str
    raw: dict


class ErrorResponse(BaseModel):
    error: str
    message: str
    request_id: Optional[str] = None
