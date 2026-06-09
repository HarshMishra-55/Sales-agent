"""
Memory Layer — Abstracted Interface
====================================
All memory reads/writes go through this module.
To swap SQLite → Postgres or Mem0, change ONLY this file.

The interface:
  - save_message(...)          → persist a chat message
  - get_history(user_id)       → full message history
  - save_memory_fact(...)      → store an extracted user fact
  - get_memory_facts(user_id)  → retrieve facts for context injection
  - delete_user_memory(...)    → GDPR wipe
"""

import uuid
import json
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message, UserMemoryFact, FlaggedConversation


# ── Message history ───────────────────────────────────────────────────────────

async def save_message(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: str,
    role: str,
    content: str,
    tools_called: Optional[List[str]] = None,
    eval_groundedness: Optional[float] = None,
    eval_relevance: Optional[float] = None,
    eval_confidence: Optional[float] = None,
    eval_flagged: Optional[bool] = None,
    eval_reasoning: Optional[str] = None,
) -> Message:
    msg = Message(
        id=str(uuid.uuid4()),
        user_id=user_id,
        session_id=session_id,
        role=role,
        content=content,
        tools_called=tools_called or [],
        eval_groundedness=eval_groundedness,
        eval_relevance=eval_relevance,
        eval_confidence=eval_confidence,
        eval_flagged=eval_flagged,
        eval_reasoning=eval_reasoning,
        timestamp=datetime.now(timezone.utc),
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return msg


async def get_history(db: AsyncSession, user_id: str) -> List[Message]:
    result = await db.execute(
        select(Message)
        .where(Message.user_id == user_id)
        .order_by(Message.timestamp.asc())
    )
    return result.scalars().all()


async def get_recent_session_messages(
    db: AsyncSession, session_id: str, limit: int = 20
) -> List[Message]:
    """Fetch the N most recent messages in a session for context window."""
    result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.timestamp.desc())
        .limit(limit)
    )
    msgs = result.scalars().all()
    return list(reversed(msgs))  # chronological order


# ── Long-term memory facts ────────────────────────────────────────────────────

async def save_memory_fact(
    db: AsyncSession,
    *,
    user_id: str,
    fact: str,
    source_session: str,
) -> UserMemoryFact:
    fact_obj = UserMemoryFact(
        id=str(uuid.uuid4()),
        user_id=user_id,
        fact=fact,
        source_session=source_session,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(fact_obj)
    await db.commit()
    await db.refresh(fact_obj)
    return fact_obj


async def get_memory_facts(db: AsyncSession, user_id: str) -> List[UserMemoryFact]:
    result = await db.execute(
        select(UserMemoryFact)
        .where(UserMemoryFact.user_id == user_id)
        .order_by(UserMemoryFact.created_at.asc())
    )
    return result.scalars().all()


# ── GDPR / memory wipe ────────────────────────────────────────────────────────

async def delete_user_memory(db: AsyncSession, user_id: str) -> int:
    """Deletes all messages, facts, and flags for a user. Returns total rows deleted."""
    r1 = await db.execute(delete(Message).where(Message.user_id == user_id))
    r2 = await db.execute(delete(UserMemoryFact).where(UserMemoryFact.user_id == user_id))
    r3 = await db.execute(
        delete(FlaggedConversation).where(FlaggedConversation.user_id == user_id)
    )
    await db.commit()
    return r1.rowcount + r2.rowcount + r3.rowcount


# ── Flagged conversations ─────────────────────────────────────────────────────

async def save_flag(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: str,
    reason: str,
) -> FlaggedConversation:
    flag = FlaggedConversation(
        id=str(uuid.uuid4()),
        user_id=user_id,
        session_id=session_id,
        reason=reason,
        resolved=False,
        created_at=datetime.now(timezone.utc),
    )
    db.add(flag)
    await db.commit()
    await db.refresh(flag)
    return flag


async def get_flags(db: AsyncSession, user_id: str) -> List[FlaggedConversation]:
    result = await db.execute(
        select(FlaggedConversation)
        .where(FlaggedConversation.user_id == user_id)
        .order_by(FlaggedConversation.created_at.desc())
    )
    return result.scalars().all()
