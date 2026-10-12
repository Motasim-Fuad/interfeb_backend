from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.base_model import UUIDMixin
from app.core.database import Base


class AssistantConversation(Base, UUIDMixin):
    __tablename__ = "assistant_conversations"
    __table_args__ = (Index("ix_assistant_conversations_owner", "owner_id", "updated_at"),)

    owner_id = Column(String, nullable=False)
    title = Column(String, nullable=False, default="New chat")
    # Last resolved intent + slots, so follow-ups like "details dao" or "only failed" refine it.
    context = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    messages = relationship(
        "AssistantMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AssistantMessage.created_at",
    )


class AssistantMessage(Base, UUIDMixin):
    __tablename__ = "assistant_messages"
    __table_args__ = (Index("ix_assistant_messages_conversation", "conversation_id", "created_at"),)

    conversation_id = Column(UUID(as_uuid=True), ForeignKey("assistant_conversations.id", ondelete="CASCADE"), nullable=False)
    role = Column(String, nullable=False)  # user | assistant
    content = Column(Text, nullable=False, default="")
    blocks = Column(JSON, nullable=False, default=list)
    meta = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    conversation = relationship("AssistantConversation", back_populates="messages")
