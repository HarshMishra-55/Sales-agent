# Persistent Sales Assistant Agent

A production-grade conversational API where the agent **remembers context across sessions**, uses **real tools** to answer product questions, and produces a **self-evaluation score on every response**.

> Built for the NexaSaaS B2B sales assistant take-home assignment.

---

## Live URL

```
https://<your-railway-url>.railway.app
```

---

## Architecture Diagram

```
User Request
     │
     ▼
┌─────────────────────────────────────────────────────────┐
│                   FastAPI  (main.py)                     │
│  POST /chat/{user_id}                                    │
│       ↓                                                  │
│  api/routes.py  ──────────────────────────────────────► │
│       ↓                                                  │
│  services/chat_service.py  (Agent Loop)                  │
│       │                                                  │
│       ├─ 1. Load session history (memory/store.py)       │
│       │        └── SQLite: messages table                │
│       │                                                  │
│       ├─ 2. Claude API call (tools=[search_catalog,      │
│       │                      get_user_memory,            │
│       │                      flag_for_human])            │
│       │                                                  │
│       ├─ 3. Tool execution (tools/catalog_tools.py)      │
│       │      ├─ search_catalog  → catalog.json keyword   │
│       │      ├─ get_user_memory → SQLite: user_memory_   │
│       │      │                    facts table            │
│       │      └─ flag_for_human  → SQLite: flagged_       │
│       │                          conversations table     │
│       │                                                  │
│       ├─ 4. Final response (Claude end_turn)             │
│       │                                                  │
│       ├─ 5. Extract + persist new memory facts           │
│       │        └── memory/store.py → save_memory_fact()  │
│       │                                                  │
│       ├─ 6. Self-eval (services/eval_service.py)         │
│       │        └── Claude scores its own response        │
│       │                                                  │
│       └─ 7. Persist assistant message + eval to DB       │
│                                                          │
└──────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────┐
│  ChatResponse                                            │
│  {                                                       │
│    "response": "...",                                    │
│    "eval": {                                             │
│      "groundedness": 0.91,                               │
│      "relevance": 0.88,                                  │
│      "confidence": 0.85,                                 │
│      "flagged": false,                                   │
│      "reasoning": "..."                                  │
│    },                                                    │
│    "tools_called": ["get_user_memory", "search_catalog"],│
│    "session_id": "uuid",                                 │
│    "user_id": "alice"                                    │
│  }                                                       │
└──────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
sales-agent/
├── main.py                        # FastAPI app + lifespan (DB init)
├── catalog.json                   # Product/pricing catalog
├── requirements.txt
├── railway.toml                   # Railway deployment config
├── Procfile
└── app/
    ├── api/
    │   └── routes.py              # Route handlers only — no logic
    ├── agents/                    # (future: multi-agent coordination)
    ├── memory/
    │   └── store.py               # ← SWAPPABLE memory abstraction
    ├── tools/
    │   └── catalog_tools.py       # search_catalog, get_user_memory, flag_for_human
    ├── services/
    │   ├── chat_service.py        # Agent loop + fact extraction
    │   └── eval_service.py        # Self-scoring logic
    ├── models/
    │   └── schemas.py             # Pydantic request/response models
    └── db/
        ├── models.py              # SQLAlchemy ORM models
        └── session.py             # Engine, session factory, health check
```

---

## Memory Design Decision

### What I built

Memory is split into two layers, both stored in SQLite via SQLAlchemy async:

| Layer | Table | Purpose |
|---|---|---|
| **Short-term** | `messages` | Every chat turn — used to reconstruct session context |
| **Long-term** | `user_memory_facts` | Extracted durable facts about the user — injected into every new session via `get_user_memory` tool |

The `memory/store.py` module is the **only file** that touches the database for memory operations. All reads and writes go through its functions (`save_message`, `get_memory_facts`, `delete_user_memory`, etc.). Swapping to Postgres or a dedicated memory service like Mem0 means changing **one file**, not ten.

### Why this design

- **Session continuity without payload bloat**: the frontend sends only `{"message": "..."}`. Context is retrieved server-side.
- **Fact extraction separates signal from noise**: instead of replaying 200 raw messages, we extract 5–10 durable facts ("User wants Enterprise SSO", "Budget ~$400/mo") and inject those. This keeps the context window lean.
- **GDPR compliance is trivial**: `DELETE /chat/{user_id}/memory` calls three `DELETE WHERE user_id = ?` statements — all data is gone.

### What I'd use at scale

| Scale | Memory backend |
|---|---|
| MVP / <10K users | SQLite (current) |
| Production / multi-instance | PostgreSQL (change `DATABASE_URL`, same code) |
| 100K+ users with semantic recall | [Mem0](https://mem0.ai) or [Zep](https://getzep.com) — swap `memory/store.py` to their SDK |
| Enterprise with compliance | PostgreSQL + pgvector for semantic search over past conversations |

---

## Eval Design

### How it works

After every agent response, a second Claude call scores the response on three dimensions:

- **Groundedness** (0–1): Is the answer backed by catalog data or retrieved context?
- **Relevance** (0–1): Does it directly answer what the user asked?
- **Confidence** (0–1): Overall correctness + helpfulness estimate

If confidence < 0.65, `flagged: true` is set and a `FlaggedConversation` row is written to the DB — visible to a human reviewer via `GET /chat/{user_id}/evals`.

### Limitations

1. **Optimism bias**: self-scoring LLMs tend to rate their own answers generously. Real RAGAS-style eval uses retrieved reference docs to ground the score.
2. **No ground truth**: we can't verify whether the answer was factually correct without a reference answer.
3. **Latency cost**: the eval adds ~1 extra Claude API call per turn.

### What I'd replace it with at scale

- [RAGAS](https://docs.ragas.io) — faithfulness + answer relevance against retrieved context
- [TruLens](https://www.trulens.org) — RAG triad evaluation (groundedness, context relevance, answer relevance)
- Dedicated eval LLM with reference docs passed in (not self-scoring)
- Async eval pipeline: return response immediately, score in background, store asynchronously

---

## Cross-Session Memory Demo (curl)

### Call 1 — Establish context (Session A)

```bash
curl -s -X POST "https://<your-railway-url>.railway.app/chat/alice" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Hi, I have a team of 30 people and our main requirement is SSO. What plan should I look at?"
  }' | python3 -m json.tool
```

Expected: Agent calls `get_user_memory` (empty first time) + `search_catalog`, recommends Enterprise plan, stores facts: *"User has a team of 30 people"*, *"User requires SSO"*, *"Interested in Enterprise plan"*.

---

### Call 2 — New session, memory persists

```bash
curl -s -X POST "https://<your-railway-url>.railway.app/chat/alice" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Does that plan include audit logs?"
  }' | python3 -m json.tool
```

Expected: Agent calls `get_user_memory` → retrieves *"Interested in Enterprise plan"* from the previous session → knows "that plan" refers to Enterprise → correctly confirms audit logs are included. **No session ID re-sent. Memory is entirely server-side.**

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/chat/{user_id}` | Send message, get response + eval |
| `GET` | `/chat/{user_id}/history` | Full conversation history |
| `DELETE` | `/chat/{user_id}/memory` | GDPR memory wipe |
| `GET` | `/chat/{user_id}/evals` | Aggregated eval scores |
| `GET` | `/catalog` | Product/pricing catalog |
| `GET` | `/health` | Service health check |

Full interactive docs at `/docs` (Swagger UI).

---

## Sample Response

```json
{
  "response": "Based on your team size of 30 and SSO requirement, the Enterprise plan at $499/month is the right fit. It includes unlimited users, SSO (SAML + OIDC), audit logs, and a 99.9% SLA.",
  "eval": {
    "groundedness": 0.93,
    "relevance": 0.95,
    "confidence": 0.91,
    "flagged": false,
    "reasoning": "Response sourced directly from catalog Enterprise plan entry. User context (SSO requirement, team size) applied correctly."
  },
  "tools_called": ["get_user_memory", "search_catalog"],
  "session_id": "3f8a1c2d-...",
  "user_id": "alice",
  "timestamp": "2026-06-09T10:30:00Z"
}
```

---

## Local Setup

```bash
# 1. Clone and install
git clone https://github.com/yourusername/sales-agent
cd sales-agent
pip install -r requirements.txt

# 2. Set environment variables
cp .env.example .env
# Edit .env — add your ANTHROPIC_API_KEY

# 3. Run
uvicorn main:app --reload

# 4. Open docs
open http://localhost:8000/docs
```

---

## Railway Deployment

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login and deploy
railway login
railway init
railway up

# Set environment variable
railway variables set ANTHROPIC_API_KEY=sk-ant-...
```

The `railway.toml` configures the start command and health check path automatically.

---

## Tech Stack

- **FastAPI** — async API framework
- **SQLAlchemy (async)** + **aiosqlite** — DB layer
- **Anthropic Claude** (`claude-sonnet-4-20250514`) — agent + eval
- **Pydantic v2** — request/response validation
- **Railway** — hosting
