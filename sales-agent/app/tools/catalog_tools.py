"""
Tools Module
============
Real callable functions registered as Anthropic tool definitions.
Each tool has:
  - A tool_spec (dict) for the Claude API tools list
  - An async execute function
"""

import json
import os
from typing import Any, Dict, List

from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.store import get_memory_facts, save_flag


# ── Load catalog once at import time ─────────────────────────────────────────

_CATALOG_PATH = os.getenv("CATALOG_PATH", "catalog.json")

def _load_catalog() -> Dict:
    try:
        with open(_CATALOG_PATH, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"error": f"Catalog file not found at {_CATALOG_PATH}"}

CATALOG: Dict = _load_catalog()


# ── Tool specs (Anthropic API format) ─────────────────────────────────────────

TOOL_SPECS = [
    {
        "name": "search_catalog",
        "description": (
            "Search the product catalog for pricing, plan features, FAQs, and add-ons. "
            "Use this whenever the user asks about plans, pricing, features, or comparisons."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query, e.g. 'enterprise SSO features' or 'pricing'",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_user_memory",
        "description": (
            "Retrieves stored facts and preferences about this user from past sessions. "
            "Always call this at the start of every conversation to personalise your response."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "The user's unique identifier",
                }
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "flag_for_human",
        "description": (
            "Escalate this conversation to a human reviewer. "
            "Use when confidence is below 0.6, the user seems frustrated, "
            "or the question is outside the product scope."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "The user's unique identifier",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this conversation is being escalated",
                },
            },
            "required": ["user_id", "reason"],
        },
    },
]


# ── Tool execution functions ──────────────────────────────────────────────────

def execute_search_catalog(query: str) -> str:
    """
    Keyword-based search across the catalog.
    At scale this would use a vector DB; here we do structured keyword matching.
    """
    query_lower = query.lower()
    results = []

    # Search plans
    for plan in CATALOG.get("plans", []):
        plan_text = (
            plan["name"].lower()
            + " " + plan.get("price", "").lower()
            + " " + " ".join(plan.get("features", [])).lower()
            + " " + plan.get("ideal_for", "").lower()
        )
        if any(word in plan_text for word in query_lower.split()):
            results.append({"type": "plan", "data": plan})

    # Search FAQ
    for item in CATALOG.get("faq", []):
        if any(word in item["q"].lower() or word in item["a"].lower()
               for word in query_lower.split()):
            results.append({"type": "faq", "data": item})

    # Search add-ons
    for addon in CATALOG.get("addons", []):
        if any(word in addon["name"].lower() for word in query_lower.split()):
            results.append({"type": "addon", "data": addon})

    if not results:
        # Return full catalog summary if no specific match
        return json.dumps({
            "message": "No specific match found. Here is the full catalog.",
            "catalog": CATALOG,
        })

    return json.dumps({"results": results, "total": len(results)})


async def execute_get_user_memory(user_id: str, db: AsyncSession) -> str:
    """Queries the DB for stored user facts."""
    facts = await get_memory_facts(db, user_id)
    if not facts:
        return json.dumps({"user_id": user_id, "facts": [], "message": "No memory found for this user."})

    return json.dumps({
        "user_id": user_id,
        "facts": [
            {
                "fact": f.fact,
                "noted_in_session": f.source_session,
                "recorded_at": f.created_at.isoformat(),
            }
            for f in facts
        ],
        "count": len(facts),
    })


async def execute_flag_for_human(user_id: str, reason: str, session_id: str, db: AsyncSession) -> str:
    """Persists a flag record for human review."""
    flag = await save_flag(db, user_id=user_id, session_id=session_id, reason=reason)
    return json.dumps({
        "flagged": True,
        "flag_id": flag.id,
        "user_id": user_id,
        "reason": reason,
        "message": "Conversation escalated to human review queue.",
    })


async def dispatch_tool(
    tool_name: str,
    tool_input: Dict[str, Any],
    *,
    db: AsyncSession,
    session_id: str,
) -> str:
    """Routes a tool call to the correct executor."""
    if tool_name == "search_catalog":
        return execute_search_catalog(tool_input["query"])

    elif tool_name == "get_user_memory":
        return await execute_get_user_memory(tool_input["user_id"], db)

    elif tool_name == "flag_for_human":
        return await execute_flag_for_human(
            tool_input["user_id"],
            tool_input["reason"],
            session_id=session_id,
            db=db,
        )

    return json.dumps({"error": f"Unknown tool: {tool_name}"})
