"""Bot configuration, loaded entirely from environment variables (see .env.example)."""

from __future__ import annotations

import os


def _req(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise SystemExit(f"Missing required environment variable: {name}")
    return val


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


# --- Discord ---
TOKEN = _req("DISCORD_BOT_TOKEN")
GUILD_ID = _int("DISCORD_GUILD_ID", 0)          # 0 = sync commands globally (slow to propagate)
ANNOUNCE_CHANNEL_ID = _int("ANNOUNCE_CHANNEL_ID", 0)
OOC_CHANNEL_ID = _int("OOC_CHANNEL_ID", 0)
AHELP_CHANNEL_ID = _int("AHELP_CHANNEL_ID", 0)

# --- Game (BYOND world topic) ---
GAME_HOST = os.getenv("GAME_HOST", "127.0.0.1").strip()
GAME_PORT = _int("GAME_PORT", 1337)
GAME_COMMS_KEY = _req("GAME_COMMS_KEY")          # must match COMMS_KEY in config/comms.txt

# --- Inbound HTTP ingest (game -> bot) ---
INGEST_HOST = os.getenv("BOT_INGEST_HOST", "127.0.0.1").strip()
INGEST_PORT = _int("BOT_INGEST_PORT", 5000)
INGEST_SECRET = _req("BOT_INGEST_SECRET")        # must match DISCORD_BOT_SECRET in config/secrets.txt

# --- Behaviour ---
PRESENCE_INTERVAL = _int("PRESENCE_INTERVAL", 60)  # seconds between status polls for bot presence

# --- RAG /ask command (local llama.cpp sidecars; see rag.py) ---
RAG_DB = os.getenv("RAG_DB", "/app/data/rag.sqlite").strip()
CONVO_DB = os.getenv("CONVO_DB", "/app/data/convo.sqlite").strip()  # reply-chain memory
LLM_URL = os.getenv("LLM_URL", "http://127.0.0.1:8089").strip()
EMBED_URL = os.getenv("EMBED_URL", "http://127.0.0.1:8090").strip()
RAG_TOP_K = _int("RAG_TOP_K", 5)
RAG_MIN_SCORE = float(os.getenv("RAG_MIN_SCORE", "0.45").strip() or 0.45)
ASK_COOLDOWN = _int("ASK_COOLDOWN", 15)  # per-user seconds between /ask uses
ASK_MAX_QUEUE = _int("ASK_MAX_QUEUE", 3)  # questions allowed to wait behind a running answer
# Include connected player names in /ask live answers ("is X playing?").
# Set false to expose only the player count (reduces who-is-online metagaming).
ASK_PLAYER_NAMES = os.getenv("ASK_PLAYER_NAMES", "true").strip().lower() != "false"
