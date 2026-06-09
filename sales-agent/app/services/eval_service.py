"""
Eval Service
============
Prompts Claude to self-score every response it produces.
Returns a structured EvalBlock.

Limitations (documented in README):
- Self-reported scores have an optimism bias; a separate evaluator model would be more accurate.
- Groundedness is estimated, not verified against retrieved documents.
- At scale: use RAGAS, TruLens, or a dedicated eval LLM with reference docs.
"""

import json
import anthropic
from app.models.schemas import EvalBlock

_client = anthropic.Anthropic()

EVAL_SYSTEM = """You are an objective AI response quality evaluator.
Given a user question, the agent's response, and the catalog context used,
output ONLY a JSON object — no preamble, no markdown fences — with these fields:

{
  "groundedness": <0.0–1.0, how well the response is backed by the provided catalog/context>,
  "relevance":    <0.0–1.0, how directly the response answers the user's actual question>,
  "confidence":   <0.0–1.0, overall confidence the response is correct and helpful>,
  "flagged":      <true if confidence < 0.65 or the response may mislead>,
  "reasoning":    "<one sentence explaining the scores>"
}

Be strict. Penalise vague, off-topic, or ungrounded answers.
"""


async def evaluate_response(
    user_message: str,
    assistant_response: str,
    catalog_context: str,
    tools_called: list[str],
) -> EvalBlock:
    """
    Calls Claude to self-evaluate the assistant's response.
    Falls back to a low-confidence default on any error.
    """
    try:
        eval_prompt = f"""User question: {user_message}

Agent response: {assistant_response}

Catalog context used: {catalog_context[:2000]}

Tools called: {', '.join(tools_called) if tools_called else 'none'}

Evaluate the agent response now."""

        resp = _client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            system=EVAL_SYSTEM,
            messages=[{"role": "user", "content": eval_prompt}],
        )

        raw = resp.content[0].text.strip()
        # Strip any accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)

        return EvalBlock(
            groundedness=float(data.get("groundedness", 0.5)),
            relevance=float(data.get("relevance", 0.5)),
            confidence=float(data.get("confidence", 0.5)),
            flagged=bool(data.get("flagged", False)),
            reasoning=str(data.get("reasoning", "Eval completed.")),
        )

    except Exception as e:
        # Safe fallback — never crash the main response due to eval
        return EvalBlock(
            groundedness=0.5,
            relevance=0.5,
            confidence=0.5,
            flagged=True,
            reasoning=f"Eval failed with error: {type(e).__name__}. Manual review recommended.",
        )
