# Snowywood Discord bot

A standalone sidecar that bridges the running game server and Discord. It replaces the
old one-way `DISCORD_WEBHOOK_URL` notification with a real bot.

## What it does

- **Round notifications** — round ending / ended posted as embeds in an announce channel.
- **Server status** — `/status` and `/players` slash commands, plus live bot presence ("N players | Mm").
- **OOC bridge** — messages in a chosen Discord channel appear in game OOC, and in-game OOC is mirrored back.
- **Ahelp relay** — each ticket opens a Discord thread in a staff channel; staff replies in the thread are PM'd to the player in game.
- **`/ask` game Q&A** — a fully local RAG pipeline answers player questions about jobs, gods,
  classes, skills, spells, virtues, smithing recipes, and lore. See "RAG /ask" below.

## How it talks to the game

```
game  --HTTP POST /ingest (X-Bot-Secret)-->  bot      (round events, OOC mirror, ahelp tickets)
bot   --BYOND world topic (comms key)----->  game     (status, OOC injection, admin replies)
```

The game side is already wired up:
- `send_bot_event()` in `code/__HELPERS/chat.dm` posts events to `DISCORD_BOT_URL`.
- The `discord_ooc` / `status` / `adminmsg` / `playing` handlers in `code/datums/world_topic.dm` serve the bot's requests.

## Setup

1. **Create the bot application**
   - Go to <https://discord.com/developers/applications> → New Application.
   - **Bot** tab → Reset Token → copy the token.
   - Under **Privileged Gateway Intents**, enable **Message Content Intent** (needed for the OOC/ahelp reply bridges).
   - **OAuth2 → URL Generator**: scopes `bot` + `applications.commands`; bot permissions: View Channels, Send Messages, Create Public Threads, Send Messages in Threads, Embed Links. Open the generated URL to invite the bot.

2. **Get channel IDs** — enable Developer Mode (User Settings → Advanced), right-click each channel → Copy ID, for the announce / OOC / ahelp channels.

3. **Configure the game** (already templated):
   - `config/config.txt`: `DISCORD_BOT_URL http://127.0.0.1:5000/ingest`
   - `config/secrets.txt` (gitignored): set `DISCORD_BOT_SECRET` to a random value (`openssl rand -hex 32`).
   - `config/comms.txt`: make sure `COMMS_KEY` is set (the bot needs the same value).

4. **Configure the bot**
   ```bash
   cd tools/discord_bot
   cp .env.example .env
   # fill in token, channel IDs, GAME_COMMS_KEY (= COMMS_KEY), BOT_INGEST_SECRET (= DISCORD_BOT_SECRET)
   ```

5. **Run it**
   ```bash
   ./run.sh
   ```
   This builds the image and runs it with `--network host --restart unless-stopped`, so it
   auto-starts on reboot like the game and database containers. Logs: `docker logs -f snowywood-discord-bot`.

6. **Apply the game side** — the new config/topic handlers take effect after the next compile + round restart.

## RAG /ask

Fully local question answering — no API keys, ~1.1 GB RAM total, strictly grounded in
game data extracted from this codebase.

```
rag_extract.py   .dm sources + books  ->  data/corpus.jsonl     (no deps, run on host)
rag_ingest.py    corpus + embed server -> data/rag.sqlite       (no deps, run on host)
rag.py           runtime: retrieve top-k chunks -> tiny LLM answers ONLY from them
```

Two llama.cpp sidecars live in `/home/blob/docker-compose.yml`:

- `snowywood-llm` (port 8089): Qwen3-0.6B Q4_K_M, ~600 MB. Generation, temperature 0.
  Set `LLM_MODEL` in the compose environment to swap in a bigger gguf from `/home/blob/models`.
- `snowywood-embed` (port 8090): nomic-embed-text v1.5 Q8, ~200 MB. Embeddings.

Guardrails: the system prompt forbids answering outside the retrieved entries, retrieval
below a similarity floor (`RAG_MIN_SCORE`) short-circuits to "I don't know" without calling
the model, answers cite their source entries in the embed footer, and a per-user cooldown
(`ASK_COOLDOWN`) plus a global one-at-a-time lock keep CPU inference from stacking up.

Both models run with `--mlock` (pinned in RAM, no cold-start paging) and auto-start with
the docker daemon on boot. `/ask` also receives a **live server status** entry on every
question — current player list (via the comms-key-gated `playerlist` world topic, which
respects the anonymize list), map, round ID, round duration, and staff count — so it can
answer "is X playing right now?" or "what map is on?" alongside knowledge-base questions.

To refresh the knowledge base after game content changes:

```bash
cd tools/discord_bot
python3 rag_extract.py          # rebuild corpus from the codebase
python3 rag_ingest.py           # re-embed (needs the embed container up)
./run.sh                        # restart the bot to reload the index
```

The same `data/corpus.jsonl` is the planned input for generating wiki pages.

## Notes

- `--network host` is used so `127.0.0.1` reaches both the game (1337) and the ingest port (5000).
  Keep `BOT_INGEST_HOST=127.0.0.1` so the ingest endpoint is not exposed off-box.
- Ahelp thread↔player mappings are in-memory; after a bot restart, replies work again once the
  player sends a new ahelp (which re-creates/links the thread).
- Leave `DISCORD_WEBHOOK_URL` blank once the bot is running, or round notifications post twice.
