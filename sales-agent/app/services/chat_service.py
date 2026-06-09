"""
Chat Service
============
Orchestrates the full agent loop:
  1. Load session history + user memory facts
  2. Call Claude with tool definitions
  3. Execute any tool calls (search_catalog, get_user_memory, flag_for_human)
  4. Return final text response
  5. Extract + persist new memory facts from the exchange
  6. Run self-evaluation
  7. Persist everything to DB
"""

import json
import uuid
from datetime import datetime, timezone
from typing import List, Tuple

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.store import (
    save_message, get_recent_session_messages,
    get_memory_facts, save_memory_fact,
)
from app.tools.catalog_tools import TOOL_SPECS, dispatch_tool, CATALOG
from app.services.eval_service import evaluate_response
from app.models.schemas import ChatResponse, EvalBlock

_client = anthropic.Anthropic()

SYSTEM_PROMPT = """You are a knowledgeable, friendly sales assistant for NexaSaaS — a B2B workflow automation platform.

Your job:
- Answer questions about NexaSaaS pricing, features, and plans ACCURATELY using the search_catalog tool.
- Remember each user's context and history using the get_user_memory tool (always call it first).
- Never fabricate pricing or features — always search the catalog.
- If you are unsure or the question is out of scope, use flag_for_human.
- Be concise, professional, and helpful.

Rules:
1. ALWAYS call get_user_memory at the start of each conversation to personalise your response.
2. ALWAYS call search_catalog when pricing, features, or plan comparisons are discussed.
3. If confidence is low or the user seems frustrated, call flag_for_human.
4. Personalize responses based on past facts retrieved from memory.
"""


def _build_messages(
    session_messages: list,
    new_user_message: str,
) -> List[dict]:
    """Build the messages array for the Claude API call."""
    messages = []

    # Add recent session history
    for msg in session_messages:
        messages.append({"role": msg.role, "content": msg.content})

    # Add the new user message
    messages.append({"role": "user", "content": new_user_message})
    return messages


async def run_agent(
    user_id: str,
    user_message: str,
    session_id: str,
    db: AsyncSession,
) -> ChatResponse:
    """
    Main agent loop. Handles multi-turn tool use until Claude produces a final text response.
    """

    # 1. Fetch recent session context (last 20 messages)
    session_messages = await get_recent_session_messages(db, session_id, limit=20)

    # 2. Build messages list
    messages = _build_messages(session_messages, user_message)

    # 3. Persist user message
    await save_message(
        db,
        user_id=user_id,
        session_id=session_id,
        role="user",
        content=user_message,
    )

    # 4. Agentic loop — run until stop_reason is "end_turn"
    tools_called: List[str] = []
    catalog_context_used: str = ""
    final_response: str = ""

    while True:
        response = _client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            tools=TOOL_SPECS,
            messages=messages,
        )

        # Append the assistant's raw response to the conversation
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            # Extract final text
            for block in response.content:
                if hasattr(block, "text"):
                    final_response = block.text
            break

        if response.stop_reason == "tool_use":
            tool_results = []

            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input
                tools_called.append(tool_name)

                # Execute the tool
                result_str = await dispatch_tool(
                    tool_name,
                    tool_input,
                    db=db,
                    session_id=session_id,
                )

                # Track catalog context for eval
                if tool_name == "search_catalog":
                    catalog_context_used += result_str

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                })

            # Feed tool results back into the conversation
            messages.append({"role": "user", "content": tool_results})
            continue

        # Unexpected stop reason — break to avoid infinite loop
        break

    if not final_response:
        final_response = "I'm sorry, I wasn't able to generate a response. Please try again."

    # 5. Extract and store memory facts from this exchange
    await _extract_and_store_facts(
        db=db,
        user_id=user_id,
        session_id=session_id,
        user_message=user_message,
        assistant_response=final_response,
    )

    # 6. Self-evaluation
    eval_block = await evaluate_response(
        user_message=user_message,
        assistant_response=final_response,
        catalog_context=catalog_context_used or json.dumps(CATALOG),
        tools_called=tools_called,
    )

    # If flagged and flag_for_human wasn't already called, auto-flag
    if eval_block.flagged and "flag_for_human" not in tools_called:
        await dispatch_tool(
            "flag_for_human",
            {"user_id": user_id, "reason": f"Low confidence: {eval_block.reasoning}"},
            db=db,
            session_id=session_id,
        )

    # 7. Persist assistant response + eval
    await save_message(
        db,
        user_id=user_id,
        session_id=session_id,
        role="assistant",
        content=final_response,
        tools_called=tools_called,
        eval_groundedness=eval_block.groundedness,
        eval_relevance=eval_block.relevance,
        eval_confidence=eval_block.confidence,
        eval_flagged=eval_block.flagged,
        eval_reasoning=eval_block.reasoning,
    )

    return ChatResponse(
        response=final_response,
        eval=eval_block,
        tools_called=tools_called,
        session_id=session_id,
        user_id=user_id,
        timestamp=datetime.now(timezone.utc),
    )


async def _extract_and_store_facts(
    db: AsyncSession,
    user_id: str,
    session_id: str,
    user_message: str,
    assistant_response: str,
) -> None:
    """
    Prompts Claude to extract durable user facts from the exchange
    and stores them in the long-term memory layer.
    """
    extract_prompt = f"""You are a memory extractor. 
Given a user message and an assistant response from a sales conversation, 
extract any DURABLE FACTS about the user that would be useful in future sessions.

Examples of durable facts:
- "User is interested in the Enterprise plan"
- "User asked about SSO support"  
- "User has a team of 30 people"
- "User mentioned budget is under $300/mo"

If there are no durable facts to extract, output only: []

Output ONLY a JSON array of short fact strings. No markdown. No explanation.

User message: {user_message}
Assistant response: {assistant_response}"""

    try:
        resp = _client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[{"role": "user", "content": extract_prompt}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        facts = json.loads(raw)
        if isinstance(facts, list):
            for fact in facts:
                if isinstance(fact, str) and fact.strip():
                    await save_memory_fact(
                        db,
                        user_id=user_id,
                        fact=fact.strip(),
                        source_session=session_id,
                    )
    except Exception:
        # Memory extraction failure is non-fatal
        pass
