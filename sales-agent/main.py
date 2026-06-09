"""
main.py — Application Entry Point
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from app.db.session import init_db
from app.api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise DB tables on startup."""
    await init_db()
    yield


app = FastAPI(
    title="Persistent Sales Assistant API",
    description=(
        "A production-grade AI sales agent with cross-session memory, "
        "tool use (catalog search + user memory), and self-evaluation on every response."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
