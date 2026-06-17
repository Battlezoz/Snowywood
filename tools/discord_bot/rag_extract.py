"""Build the RAG corpus from the Snowywood codebase.

Parses player-facing descriptive data straight out of the .dm sources (jobs,
advclasses, patrons, species, skills, spells, virtues, vices, anvil recipes)
plus the in-game lore books and tips, and writes one JSON document per entry
to data/corpus.jsonl. Run on the host:

    python3 rag_extract.py [--repo /home/blob/Snowywood]

No third-party dependencies. The same corpus later feeds wiki generation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HEADER_RE = re.compile(r"^(/(?:datum|obj|mob|atom)[\w/]*)\s*(?://.*)?$")
VAR_RE = re.compile(r"^\t+(?:var/)?(\w+)\s*=\s*(.+)$")
STRING_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
SPAN_RE = re.compile(r"<[^>]+>|\\n")


def _logical_lines(text: str):
    """Yield lines with DM backslash continuations joined."""
    buf = ""
    for line in text.splitlines():
        line = line.rstrip("\r")
        if buf:
            line = buf + " " + line.lstrip()
            buf = ""
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            buf = stripped[:-1].rstrip()
            continue
        yield line
    if buf:
        yield buf


def _clean_string(raw: str) -> str | None:
    """Extract and unescape the quoted string portion of a DM value."""
    parts = STRING_RE.findall(raw)
    if not parts:
        return None
    text = " ".join(parts)
    text = text.replace('\\"', '"').replace("\\'", "'")
    text = SPAN_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip() or None


def parse_dm_blocks(path: Path) -> dict[str, dict[str, str]]:
    """Map typepath -> {var: raw_value} for column-0 datum blocks in a file."""
    blocks: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return blocks
    for line in _logical_lines(text):
        m = HEADER_RE.match(line)
        if m:
            # Proc definitions look like headers but contain '(' — HEADER_RE
            # already rejects them; nested proc bodies start a new block scope.
            current = blocks.setdefault(m.group(1), {})
            continue
        if line and not line.startswith("\t"):
            current = None  # proc bodies, defines, etc.
            continue
        if current is None:
            continue
        vm = VAR_RE.match(line)
        if vm and vm.group(1) not in current:
            current[vm.group(2 - 1)] = vm.group(2)
    return blocks


def parse_tree(root: Path) -> dict[str, dict[str, str]]:
    """Parse every .dm under root into one typepath map (first def wins)."""
    merged: dict[str, dict[str, str]] = {}
    for path in sorted(root.rglob("*.dm")):
        for typepath, fields in parse_dm_blocks(path).items():
            dest = merged.setdefault(typepath, {})
            dest.setdefault("__source__", str(path))
            for k, v in fields.items():
                dest.setdefault(k, v)
    return merged


def inherited(blocks: dict[str, dict[str, str]], typepath: str, var: str) -> str | None:
    """Walk up the typepath looking for var, mimicking DM inheritance."""
    parts = typepath.split("/")
    while len(parts) > 2:
        fields = blocks.get("/".join(parts))
        if fields and var in fields:
            return fields[var]
        parts.pop()
    return None


def is_abstract(fields: dict[str, str], typepath: str) -> bool:
    raw = fields.get("abstract_type", "")
    return raw.strip() == typepath


class Corpus:
    def __init__(self) -> None:
        self.docs: list[dict] = []
        self._seen: set[str] = set()

    def add(self, category: str, title: str, text: str, source: str) -> None:
        text = re.sub(r"\s+", " ", text).strip()
        if not text or len(text) < 20:
            return
        doc_id = f"{category}:{title}".lower()
        if doc_id in self._seen:
            return
        self._seen.add(doc_id)
        self.docs.append(
            {"id": doc_id, "category": category, "title": title, "text": text, "source": source}
        )


def extract_jobs(repo: Path, corpus: Corpus) -> None:
    blocks = parse_tree(repo / "code/modules/jobs/job_types")
    for typepath, f in blocks.items():
        if not typepath.startswith("/datum/job") or is_abstract(f, typepath):
            continue
        title = _clean_string(f.get("title", ""))
        tutorial = _clean_string(f.get("tutorial", ""))
        if not title or not tutorial:
            continue
        bits = [f"{title} is a playable job. {tutorial}"]
        f_title = _clean_string(f.get("f_title", ""))
        if f_title and f_title != title:
            bits.append(f"Female characters hold the title {f_title}.")
        positions = f.get("spawn_positions") or inherited(blocks, typepath, "spawn_positions")
        if positions and positions.strip().isdigit():
            bits.append(f"There are {positions.strip()} starting slots for this job.")
        corpus.add("job", title, " ".join(bits), f["__source__"])


def extract_advclasses(repo: Path, corpus: Corpus) -> None:
    blocks = parse_tree(repo / "code/modules/jobs/job_types")
    for typepath, f in blocks.items():
        if "/advclass" not in typepath or is_abstract(f, typepath):
            continue
        name = _clean_string(f.get("name", ""))
        tutorial = _clean_string(f.get("tutorial", ""))
        if not name or not tutorial:
            continue
        extra = _clean_string(f.get("extra_context", "")) or ""
        corpus.add("class", name, f"{name} is a playable subclass. {tutorial} {extra}", f["__source__"])


def extract_patrons(repo: Path, corpus: Corpus) -> None:
    blocks = parse_tree(repo / "code/datums/gods")
    for typepath, f in blocks.items():
        if not typepath.startswith("/datum/patron"):
            continue
        name = _clean_string(f.get("name", ""))
        desc = _clean_string(f.get("desc", ""))
        if not name or not desc:
            continue
        kind = "an Inhumen (dark) god" if "/inhumen/" in typepath else "a god players can worship"
        bits = [f"{name} is {kind}. {desc}"]
        for var, label in (
            ("domain", "Domain"),
            ("worshippers", "Typical worshippers"),
            ("virtues", "Virtues"),
            ("sins", "Sins"),
        ):
            val = _clean_string(f.get(var, ""))
            if val:
                bits.append(f"{label}: {val}.")
        corpus.add("god", name, " ".join(bits), f["__source__"])


def extract_species(repo: Path, corpus: Corpus) -> None:
    blocks = parse_tree(repo / "code/modules/mob/living/carbon/human/species_types")
    for typepath, f in blocks.items():
        if not typepath.startswith("/datum/species"):
            continue
        name = _clean_string(f.get("name", ""))
        desc = _clean_string(f.get("desc", ""))
        if not name or not desc:
            continue
        corpus.add("species", name, f"{name} is a playable race. {desc}", f["__source__"])


def extract_skills(repo: Path, corpus: Corpus) -> None:
    blocks = parse_tree(repo / "code/datums/skills")
    for typepath, f in blocks.items():
        if not typepath.startswith("/datum/skill") or is_abstract(f, typepath):
            continue
        name = _clean_string(f.get("name", ""))
        desc = _clean_string(f.get("desc", ""))
        if not name or not desc:
            continue
        expert = _clean_string(f.get("expert_name", ""))
        tail = f" Experts in this skill are called {expert}." if expert else ""
        corpus.add("skill", name, f"{name} is a skill. {desc}{tail}", f["__source__"])


def extract_spells(repo: Path, corpus: Corpus) -> None:
    blocks = parse_tree(repo / "code/modules/spells")
    for typepath, f in blocks.items():
        name = _clean_string(f.get("name", ""))
        desc = _clean_string(f.get("desc", ""))
        if not name or not desc or len(desc) < 30:
            continue
        kind = "miracle" if f.get("miracle", "").strip() == "TRUE" else "spell"
        corpus.add("spell", name, f"{name} is a {kind}. {desc}", f["__source__"])


def extract_virtues_vices(repo: Path, corpus: Corpus) -> None:
    for root, category, noun in (
        (repo / "modular_azurepeak/virtues", "virtue", "virtue"),
        (repo / "code/datums/character_flaw", "vice", "character flaw (vice)"),
    ):
        if not root.exists():
            root = root.parent
        blocks = parse_tree(root)
        for typepath, f in blocks.items():
            if category == "virtue" and "/virtue" not in typepath:
                continue
            if category == "vice" and "charflaw" not in typepath and "character_flaw" not in typepath:
                continue
            name = _clean_string(f.get("name", ""))
            desc = _clean_string(f.get("desc", ""))
            if not name or not desc:
                continue
            extra = _clean_string(f.get("custom_text", "")) or ""
            corpus.add(category, name, f"{name} is a {noun}. {desc} {extra}", f["__source__"])


SKILL_LEVEL_RE = re.compile(r"SKILL_LEVEL_(\w+)")


def extract_anvil_recipes(repo: Path, corpus: Corpus) -> None:
    root = repo / "code/modules/roguetown/roguejobs/blacksmith/anvil_recipes"
    blocks = parse_tree(root)
    for typepath, f in blocks.items():
        if not typepath.startswith("/datum/anvil_recipe") or is_abstract(f, typepath):
            continue
        name = _clean_string(f.get("name", ""))
        if not name:
            continue
        bits = [f"{name} can be smithed at the anvil"]
        bar = f.get("req_bar") or inherited(blocks, typepath, "req_bar")
        if bar:
            material = bar.strip().rstrip("/").split("/")[-1]
            bits[0] += f" from a {material} ingot"
        bits[0] += "."
        needed = _clean_string(f.get("needed_item_text", ""))
        if needed:
            bits.append(f"It also requires {needed}.")
        diff = f.get("craftdiff") or inherited(blocks, typepath, "craftdiff")
        if diff:
            m = SKILL_LEVEL_RE.search(diff)
            if m:
                bits.append(f"Smithing skill needed: {m.group(1).title()}.")
        i_type = f.get("i_type") or inherited(blocks, typepath, "i_type")
        if i_type:
            cat = _clean_string(i_type)
            if cat:
                bits.append(f"Recipe category: {cat}.")
        corpus.add("smithing-recipe", name, " ".join(bits), f["__source__"])


def _json_strings(node, out: list[str]) -> None:
    if isinstance(node, str):
        if len(node) > 40:
            out.append(node)
    elif isinstance(node, list):
        for item in node:
            _json_strings(item, out)
    elif isinstance(node, dict):
        for item in node.values():
            _json_strings(item, out)


def extract_books(repo: Path, corpus: Corpus) -> None:
    decoder = json.JSONDecoder(strict=False)
    for path in sorted((repo / "strings/books").glob("*.json")):
        try:
            data = decoder.decode(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        strings: list[str] = []
        _json_strings(data, strings)
        if not strings:
            continue
        body = SPAN_RE.sub(" ", " ".join(strings))
        title = path.stem.replace("_", " ").title()
        corpus.add("book", title, f"From the in-game book '{title}': {body}", str(path))


# Hand-maintained facts that should always be in the knowledge base.
# Source: github.com/Mooshieblob1 profile (fetched 2026-06-13).
STATIC_DOCS = [
    (
        "about",
        "Mooshieblob (bot creator)",
        "Quill, this Discord bot and its /ask assistant, and the Snowywood server are "
        "run by Mooshieblob, also known as Blob (Mooshieblob1 on GitHub). Mooshieblob "
        "is Quill's creator and the lead developer and host of the Snowywood game "
        "server, which is a fork of Ratwood 2.0. Blob is a full-stack software "
        "developer from Australia, passionate about creating efficient, user-friendly "
        "applications. Website: mooshieblob.com. GitHub: github.com/Mooshieblob1. "
        "Other projects by Blob include MooshieUI, a beginner-friendly front-end for "
        "ComfyUI, and Koharu, an ML-powered manga translator written in Rust.",
    ),
    (
        "about",
        "Quill (who am I)",
        "Quill is the name of this bot: a young scribe maiden and devotee of Noc, the "
        "god of knowledge. She keeps the tomes of Snowywood and answers travelers' "
        "questions about the realm: its jobs and classes, gods, races, skills, spells, "
        "smithing recipes and lore. Ask her anything with /ask, by mentioning @Quill, "
        "or by replying to one of her answers. She only speaks from what is written "
        "in her tomes, and if her tomes hold no answer, she says so rather than "
        "guessing. She was created by Mooshieblob, the keeper of this realm.",
    ),
]


def extract_static(repo: Path, corpus: Corpus) -> None:
    for category, title, text in STATIC_DOCS:
        corpus.add(category, title, text, "rag_extract.py STATIC_DOCS")


def extract_tips(repo: Path, corpus: Corpus) -> None:
    path = repo / "strings/tips.txt"
    if not path.exists():
        return
    tips = [t.strip() for t in path.read_text(encoding="utf-8", errors="replace").splitlines()]
    tips = [t for t in tips if len(t) > 30]
    for i in range(0, len(tips), 8):
        chunk = " ".join(tips[i : i + 8])
        corpus.add("tips", f"Game tips {i // 8 + 1}", chunk, str(path))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="/home/blob/Snowywood")
    ap.add_argument("--out", default=None, help="defaults to <script dir>/data/corpus.jsonl")
    args = ap.parse_args()
    repo = Path(args.repo)
    out_path = Path(args.out) if args.out else Path(__file__).parent / "data/corpus.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    corpus = Corpus()
    extractors = (
        extract_jobs,
        extract_advclasses,
        extract_patrons,
        extract_species,
        extract_skills,
        extract_spells,
        extract_virtues_vices,
        extract_anvil_recipes,
        extract_books,
        extract_tips,
        extract_static,
    )
    for fn in extractors:
        before = len(corpus.docs)
        fn(repo, corpus)
        print(f"{fn.__name__}: {len(corpus.docs) - before} docs")

    # Overview docs: aggregate questions ("what classes are there?") can't be
    # answered from individual entries — top-k retrieval only sees 5 of 298.
    overview_specs = {
        "job": ("playable jobs", 35),
        "class": ("playable subclasses", 35),
        "god": ("gods players can worship", 35),
        "species": ("playable races", 35),
        "skill": ("skills", 35),
        "virtue": ("virtues characters can take", 35),
        "vice": ("character flaws (vices)", 35),
    }
    before = len(corpus.docs)
    for cat, (label, per_doc) in overview_specs.items():
        titles = [d["title"] for d in corpus.docs if d["category"] == cat]
        for i in range(0, len(titles), per_doc):
            part = "" if len(titles) <= per_doc else f" (part {i // per_doc + 1})"
            corpus.add(
                "overview",
                f"All {label}{part}",
                f"The game's {label} are: {', '.join(titles[i:i + per_doc])}.",
                "generated overview",
            )
    print(f"overviews: {len(corpus.docs) - before} docs")

    with out_path.open("w", encoding="utf-8") as fh:
        for doc in corpus.docs:
            fh.write(json.dumps(doc, ensure_ascii=False) + "\n")
    print(f"Wrote {len(corpus.docs)} docs to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
