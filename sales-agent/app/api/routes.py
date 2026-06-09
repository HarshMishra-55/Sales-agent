"""
API Routes
==========
Thin handlers — all logic lives in services/ and memory/.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db, check_db_health
from app.db.models import Message, FlaggedConversation
from app.memory.store import get_history, delete_user_memory, get_flags
from app.services.chat_service import run_agent
from app.models.schemas import (
    ChatRequest, ChatResponse, ConversationHistory, HistoryMessage,
    MemoryDeleteResponse, EvalBlock, EvalSummary, HealthResponse,
)
from app.tools.catalog_tools import CATALOG

router = APIRouter()


# ── POST /chat/{user_id} ──────────────────────────────────────────────────────

@router.post("/chat/{user_id}", response_model=ChatResponse, tags=["Chat"])
async def chat(
    user_id: str = Path(..., description="Unique user identifier"),
    body: ChatRequest = ...,
    db: AsyncSession = Depends(get_db),
):
    """Send a message to the sales assistant. Returns response + eval block."""
    session_id = body.session_id or str(uuid.uuid4())

    return await run_agent(
        user_id=user_id,
        user_message=body.message,
        session_id=session_id,
        db=db,
    )


# ── GET /chat/{user_id}/history ───────────────────────────────────────────────

@router.get("/chat/{user_id}/history", response_model=ConversationHistory, tags=["Chat"])
async def get_conversation_history(
    user_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
):
    """Full conversation history across all sessions for a user."""
    messages = await get_history(db, user_id)

    history_items = []
    sessions = set()

    for m in messages:
        sessions.add(m.session_id)
        eval_block = None
        if m.role == "assistant" and m.eval_groundedness is not None:
            eval_block = EvalBlock(
                groundedness=m.eval_groundedness,
                relevance=m.eval_relevance,
                confidence=m.eval_confidence,
                flagged=m.eval_flagged or False,
                reasoning=m.eval_reasoning or "",
            )
        history_items.append(
            HistoryMessage(
                role=m.role,
                content=m.content,
                session_id=m.session_id,
                timestamp=m.timestamp,
                tools_called=m.tools_called,
                eval=eval_block,
            )
        )

    return ConversationHistory(
        user_id=user_id,
        total_messages=len(history_items),
        sessions=list(sessions),
        messages=history_items,
    )


# ── DELETE /chat/{user_id}/memory ─────────────────────────────────────────────

@router.delete("/chat/{user_id}/memory", response_model=MemoryDeleteResponse, tags=["Chat"])
async def wipe_user_memory(
    user_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
):
    """GDPR-style memory wipe — deletes all data for a user."""
    deleted = await delete_user_memory(db, user_id)
    return MemoryDeleteResponse(
        user_id=user_id,
        deleted=True,
        message=f"Deleted {deleted} records for user '{user_id}'.",
    )


# ── GET /chat/{user_id}/evals ─────────────────────────────────────────────────

@router.get("/chat/{user_id}/evals", response_model=EvalSummary, tags=["Evals"])
async def get_eval_summary(
    user_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
):
    """Aggregated eval scores across all sessions for a user."""
    result = await db.execute(
        select(Message).where(
            Message.user_id == user_id,
            Message.role == "assistant",
            Message.eval_confidence.isnot(None),
        )
    )
    assistant_msgs = result.scalars().all()

    if not assistant_msgs:
        raise HTTPException(status_code=404, detail="No eval data found for this user.")

    total = len(assistant_msgs)
    avg_g = sum(m.eval_groundedness for m in assistant_msgs) / total
    avg_r = sum(m.eval_relevance for m in assistant_msgs) / total
    avg_c = sum(m.eval_confidence for m in assistant_msgs) / total
    flagged = sum(1 for m in assistant_msgs if m.eval_flagged)
    high_conf = sum(1 for m in assistant_msgs if m.eval_confidence and m.eval_confidence >= 0.75)

    return EvalSummary(
        user_id=user_id,
        total_responses=total,
        avg_groundedness=round(avg_g, 3),
        avg_relevance=round(avg_r, 3),
        avg_confidence=round(avg_c, 3),
        flagged_count=flagged,
        high_confidence_pct=round(high_conf / total * 100, 1),
    )


# ── GET /catalog ──────────────────────────────────────────────────────────────

@router.get("/catalog", tags=["Catalog"])
async def get_catalog():
    """Returns the product and pricing catalog."""
    return CATALOG


# ── GET /health ───────────────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse, tags=["Meta"])
async def health_check():
    """Service health check."""
    db_status = await check_db_health()
    return HealthResponse(
        status="ok" if db_status == "ok" else "degraded",
        db=db_status,
        catalog_loaded=bool(CATALOG and "plans" in CATALOG),
        timestamp=datetime.now(timezone.utc),
    )
