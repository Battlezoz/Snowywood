"""Embed the extracted corpus into the RAG index (sqlite + float32 blobs).

Reads data/corpus.jsonl (from rag_extract.py), embeds every document through
the llama.cpp embedding server, and writes data/rag.sqlite. Run on the host
after the embed container is up:

    python3 rag_ingest.py [--embed-url http://127.0.0.1:8090]

Stdlib only — embeddings are stored as packed float32 blobs, no numpy needed
here (the bot uses numpy to load them).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import struct
import sys
import urllib.request
from pathlib import Path

# nomic-embed models require task prefixes; documents and queries differ.
DOC_PREFIX = "search_document: "
BATCH = 16
MAX_CHARS = 1500  # safe for the embed server's 2048-token context
OVERLAP = 200


def chunk(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text]
    out = []
    start = 0
    while start < len(text):
        end = min(start + MAX_CHARS, len(text))
        if end < len(text):
            space = text.rfind(" ", start + MAX_CHARS - 300, end)
            if space > start:
                end = space
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = end - OVERLAP
    return [c for c in out if c]


def embed_batch(url: str, texts: list[str]) -> list[list[float]]:
    body = json.dumps({"input": [DOC_PREFIX + t for t in texts]}).encode()
    req = urllib.request.Request(
        f"{url.rstrip('/')}/v1/embeddings", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return [item["embedding"] for item in data["data"]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embed-url", default="http://127.0.0.1:8090")
    ap.add_argument("--corpus", default=None)
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    here = Path(__file__).parent
    corpus_path = Path(args.corpus) if args.corpus else here / "data/corpus.jsonl"
    db_path = Path(args.db) if args.db else here / "data/rag.sqlite"

    docs = [json.loads(line) for line in corpus_path.open(encoding="utf-8")]
    rows: list[tuple[str, str, str, str]] = []  # (doc_id, category, title, chunk_text)
    for doc in docs:
        for i, piece in enumerate(chunk(doc["text"])):
            rows.append((f"{doc['id']}#{i}", doc["category"], doc["title"], piece))
    print(f"{len(docs)} docs -> {len(rows)} chunks; embedding via {args.embed_url}")

    db_path.unlink(missing_ok=True)
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE chunks (id TEXT PRIMARY KEY, category TEXT, title TEXT,"
        " text TEXT, embedding BLOB, dim INTEGER)"
    )
    done = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i : i + BATCH]
        vectors = embed_batch(args.embed_url, [r[3] for r in batch])
        for (cid, cat, title, text), vec in zip(batch, vectors):
            blob = struct.pack(f"<{len(vec)}f", *vec)
            con.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?)",
                        (cid, cat, title, text, blob, len(vec)))
        done += len(batch)
        if done % 160 < BATCH:
            print(f"  {done}/{len(rows)}")
            con.commit()
    con.commit()
    con.close()
    print(f"Index written to {db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
