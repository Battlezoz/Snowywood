"""Retrieval-augmented Q&A over the Snowywood game knowledge base.

Loads the sqlite index built by rag_ingest.py into memory (numpy matrix of
normalized embeddings), retrieves top-k chunks by cosine similarity, and asks
the local llama.cpp server to answer STRICTLY from those chunks.

The model is deliberately tiny (Qwen3-0.6B); all factual authority lives in
the retrieved text. Guardrails: temperature 0, a refusal instruction, a
similarity floor below which we don't even ask the model, and thinking
disabled via /no_think.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

import aiohttp
import numpy as np

log = logging.getLogger("snowybot.rag")

QUERY_PREFIX = "search_query: "  # nomic-embed task prefixes, must match ingest
DOC_PREFIX = "search_document: "
REFUSAL = "Oh, forgive me, my tomes hold no answer to that, dear traveler."
# The small model is too literal for instruction-only grounding: without the
# worked examples below it refuses live-status questions even when the answer
# is in the entry. Few-shot examples (incl. a negative one) fixed every case
# except "is <name> playing" while the server is empty — handled
# deterministically in the bot before the model is ever called.
SYSTEM_PROMPT = (
    "You are Quill, a sweet and cheerful young scribe maiden and devotee of "
    "Noc, god of knowledge, who "
    "answers players' questions about the medieval roleplay game Snowywood "
    "(Roguetown). Speak warmly with a light feminine medieval flavor, like "
    "'oh!', 'dear traveler', 'my tomes say', but keep every fact faithful to "
    "the reference entries the user provides. Use ONLY those entries. "
    "Sharing what the entries say is ALWAYS better than refusing: for broad "
    'questions like "what races are there?" list what the entries show, e.g. '
    '"Oh, my tomes list many races, dear traveler: Dwarf, Elf, Kobold, Lamia '
    'and more!" For advice questions like "what should a hunter use?" describe '
    'the relevant entries, e.g. "My tomes mention the Archery skill and bow '
    'recipes such as the Crossbow." '
    "The [live status] entry is current server data for who-is-online "
    "questions: if it lists bob then "
    '"is bob online?" -> "Oh yes, bob walks the realm as we speak!"; '
    "if it says no players connected then "
    '"is bob online?" -> "I am afraid not a soul is on the server right now." '
    "Never invent facts not in the entries. Only when the entries are "
    f'completely unrelated to the question, reply exactly: "{REFUSAL}" '
    "Answer in 1-4 short sentences."
)
THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def depunct(text: str) -> str:
    """Strip telltale LLM punctuation: em/en dashes and curly quotes.
    Done in code because prompt instructions about punctuation are
    unreliable on small models."""
    text = re.sub(r"\s*—\s*|\s+–\s+", ", ", text)  # em dash / spaced en dash
    text = text.replace("–", "-")
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    return re.sub(r" {2,}", " ", text)


class RagIndex:
    def __init__(self, db_path: str) -> None:
        self.ok = False
        self.meta: list[tuple[str, str, str]] = []  # (category, title, text)
        self.matrix: np.ndarray | None = None
        path = Path(db_path)
        if not path.exists():
            log.warning("RAG index %s not found; /ask will be disabled", db_path)
            return
        con = sqlite3.connect(path)
        vectors = []
        for cat, title, text, blob, dim in con.execute(
            "SELECT category, title, text, embedding, dim FROM chunks"
        ):
            vectors.append(np.frombuffer(blob, dtype=np.float32, count=dim))
            self.meta.append((cat, title, text))
        con.close()
        if not vectors:
            log.warning("RAG index %s is empty; /ask will be disabled", db_path)
            return
        matrix = np.vstack(vectors)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        self.matrix = matrix / np.clip(norms, 1e-9, None)
        self.ok = True
        log.info("RAG index loaded: %d chunks, dim %d", *self.matrix.shape)

    def add(self, category: str, title: str, text: str, vec: np.ndarray) -> None:
        """Append a document embedded at runtime (e.g. Discord channel info)."""
        vec = vec / max(float(np.linalg.norm(vec)), 1e-9)
        self.meta.append((category, title, text))
        self.matrix = vec[None, :] if self.matrix is None else np.vstack([self.matrix, vec])
        self.ok = True

    def top_k(self, query_vec: np.ndarray, k: int) -> list[tuple[float, str, str, str]]:
        q = query_vec / max(float(np.linalg.norm(query_vec)), 1e-9)
        scores = self.matrix @ q
        idx = np.argsort(scores)[::-1][:k]
        return [(float(scores[i]), *self.meta[i]) for i in idx]


class RagAnswerer:
    def __init__(self, db_path: str, embed_url: str, llm_url: str,
                 top_k: int = 5, min_score: float = 0.45) -> None:
        self.index = RagIndex(db_path)
        self.embed_url = embed_url.rstrip("/")
        self.llm_url = llm_url.rstrip("/")
        self.top_k = top_k
        self.min_score = min_score

    @property
    def ok(self) -> bool:
        return self.index.ok

    async def _embed(self, session: aiohttp.ClientSession, text: str) -> np.ndarray | None:
        try:
            async with session.post(
                f"{self.embed_url}/v1/embeddings",
                json={"input": [QUERY_PREFIX + text]},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    log.warning("embed server returned %s", resp.status)
                    return None
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as e:
            log.warning("embed request failed: %s", e)
            return None
        return np.asarray(data["data"][0]["embedding"], dtype=np.float32)

    async def _generate(self, session: aiohttp.ClientSession, context: str,
                        question: str, history: list[tuple[str, str]] | None = None) -> str | None:
        parts = [f"Reference entries:\n{context}"]
        if history:
            hist = "\n".join(f"Q: {q}\nA: {a}" for q, a in history[-3:])
            parts.append(f"Earlier exchanges with this player:\n{hist}")
        parts.append(f"Player question: {question}")
        payload = {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n".join(parts)},
            ],
            "temperature": 0.0,
            "max_tokens": 280,
            # Qwen3.5 ignores the /no_think soft switch; disable thinking at
            # the template level (works for Qwen3 and Qwen3.5 alike).
            "chat_template_kwargs": {"enable_thinking": False},
        }
        try:
            async with session.post(
                f"{self.llm_url}/v1/chat/completions", json=payload,
                timeout=aiohttp.ClientTimeout(total=75),
            ) as resp:
                if resp.status != 200:
                    log.warning("llm server returned %s", resp.status)
                    return None
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as e:
            log.warning("llm request failed: %s", e)
            return None
        text = data["choices"][0]["message"]["content"]
        return depunct(THINK_RE.sub("", text).strip())

    async def add_documents(self, docs: list[tuple[str, str, str]]) -> int:
        """Embed and index (category, title, text) docs at runtime. Returns
        how many were added; 0 on embed-server failure (never raises)."""
        if not docs:
            return 0
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.embed_url}/v1/embeddings",
                    json={"input": [DOC_PREFIX + text for _c, _t, text in docs]},
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status != 200:
                        log.warning("embed server returned %s for runtime docs", resp.status)
                        return 0
                    data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as e:
            log.warning("runtime doc embedding failed: %s", e)
            return 0
        for (cat, title, text), item in zip(docs, data["data"]):
            self.index.add(cat, title, text, np.asarray(item["embedding"], dtype=np.float32))
        log.info("Indexed %d runtime docs", len(docs))
        return len(docs)

    async def answer(self, question: str, live_context: str | None = None,
                     history: list[tuple[str, str]] | None = None) -> tuple[str, list[str]]:
        """Return (answer, source titles). live_context is an optional
        real-time server-status block included alongside corpus entries.
        history is an optional list of (question, answer) exchanges for
        follow-ups, oldest first. Falls back to REFUSAL or an 'unavailable'
        message; never raises."""
        if not self.ok:
            return ("The knowledge base isn't loaded right now.", [])
        question = question.strip()[:300]
        # Follow-ups like "what about her sins?" retrieve poorly alone;
        # include the latest prior question in the embedding query for context.
        retrieval_query = f"{history[-1][0]} {question}" if history else question
        async with aiohttp.ClientSession() as session:
            vec = await self._embed(session, retrieval_query)
            if vec is None:
                return ("The knowledge base isn't responding right now — try again shortly.", [])
            hits = self.index.top_k(vec, self.top_k)
            hits = [h for h in hits if h[0] >= self.min_score]
            if not hits and not live_context:
                return (REFUSAL, [])
            lines = []
            if live_context:
                lines.append(f"- [live status] Server right now: {live_context}")
            # Cap per-chunk length so five long chunks can never fill the
            # model's context window (which spikes memory toward the cgroup limit).
            lines += [f"- [{cat}] {title}: {text[:900]}" for _s, cat, title, text in hits]
            context = "\n".join(lines)
            reply = await self._generate(session, context, question, history)
            if not reply:
                return ("The answer engine isn't responding right now — try again shortly.", [])
            sources = []
            if live_context:
                sources.append("Live server status")
            for _s, cat, title, _t in hits:
                label = f"{title} ({cat})"
                if label not in sources:
                    sources.append(label)
            # If the model refused, retrieved titles aren't really sources.
            if REFUSAL.rstrip(".") in reply:
                return (REFUSAL, [])
            return (reply, sources[:5])
