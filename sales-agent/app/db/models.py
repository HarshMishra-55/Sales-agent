from sqlalchemy import Column, String, Float, Boolean, DateTime, Text, JSON
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime, timezone

Base = declarative_base()


class Message(Base):
    """Stores every chat message (user + assistant) with eval data."""
    __tablename__ = "messages"

    id = Column(String, primary_key=True)          # uuid
    user_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False)           # "user" | "assistant"
    content = Column(Text, nullable=False)
    tools_called = Column(JSON, default=list)       # list[str]
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Eval fields — only populated for assistant messages
    eval_groundedness = Column(Float, nullable=True)
    eval_relevance = Column(Float, nullable=True)
    eval_confidence = Column(Float, nullable=True)
    eval_flagged = Column(Boolean, nullable=True)
    eval_reasoning = Column(Text, nullable=True)


class UserMemoryFact(Base):
    """
    Stores extracted facts about a user (interests, pain points, plan discussed, etc.)
    This is the long-term memory layer — separate from raw message history.
    """
    __tablename__ = "user_memory_facts"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    fact = Column(Text, nullable=False)             # e.g. "Interested in Enterprise SSO"
    source_session = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class FlaggedConversation(Base):
    """Tracks escalated conversations for human review."""
    __tablename__ = "flagged_conversations"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=False)
    reason = Column(Text, nullable=False)
    resolved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
