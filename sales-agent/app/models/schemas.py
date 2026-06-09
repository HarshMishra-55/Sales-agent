from pydantic import BaseModel, Field
from typing import Optional, List, Any
from datetime import datetime


# ── Request / Response schemas ────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000, description="User message")
    session_id: Optional[str] = Field(None, description="Optional session ID to continue a session")


class EvalBlock(BaseModel):
    groundedness: float = Field(..., ge=0.0, le=1.0)
    relevance: float = Field(..., ge=0.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    flagged: bool = False
    reasoning: str


class ChatResponse(BaseModel):
    response: str
    eval: EvalBlock
    tools_called: List[str]
    session_id: str
    user_id: str
    timestamp: datetime


class HistoryMessage(BaseModel):
    role: str
    content: str
    session_id: str
    timestamp: datetime
    tools_called: Optional[List[str]] = None
    eval: Optional[EvalBlock] = None


class ConversationHistory(BaseModel):
    user_id: str
    total_messages: int
    sessions: List[str]
    messages: List[HistoryMessage]


class MemoryDeleteResponse(BaseModel):
    user_id: str
    deleted: bool
    message: str


class EvalSummary(BaseModel):
    user_id: str
    total_responses: int
    avg_groundedness: float
    avg_relevance: float
    avg_confidence: float
    flagged_count: int
    high_confidence_pct: float


class HealthResponse(BaseModel):
    status: str
    db: str
    catalog_loaded: bool
    timestamp: datetime
