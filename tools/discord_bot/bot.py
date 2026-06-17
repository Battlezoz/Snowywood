"""Snowywood Discord bridge bot.

A standalone sidecar next to DreamDaemon. Two transport directions:

  game -> bot   HTTP POST to /ingest (round notifications, OOC mirror, ahelp tickets),
                authenticated with the X-Bot-Secret header.
  bot  -> game  BYOND world-topic calls (status queries, OOC injection, admin replies),
                authenticated with the comms key.

Features: round notification embeds, /status + /players slash commands, live presence,
a two-way OOC bridge, and ahelp tickets relayed to per-ticket Discord threads whose
replies are PM'd back to the player in game.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import time

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands, tasks

import config
from byond import build_query, get_status, world_topic
from rag import RagAnswerer

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("snowybot")

# In-game Quill: an OOC line mentioning this word gets a RAG answer broadcast
# back in-game via the quill_say world topic (see _on_ooc / _answer_ooc_quill).
QUILL_OOC_TRIGGER = re.compile(r"\bquill\b", re.IGNORECASE)
QUILL_OOC_COOLDOWN = 8.0  # min seconds between in-game Quill OOC answers (anti-spam)


class ConvoStore:
    """Persistent /ask exchange log keyed by the bot's answer message ID, so
    reply-chain follow-ups can recover several turns of context across
    restarts. Only the bot's own Q&As are stored — never channel chat."""

    KEEP_DAYS = 7

    def __init__(self, path: str) -> None:
        try:
            self.con = sqlite3.connect(path)
            self.con.execute(
                "CREATE TABLE IF NOT EXISTS convo (msg_id INTEGER PRIMARY KEY,"
                " parent_id INTEGER, question TEXT, answer TEXT, ts REAL)"
            )
            self.con.commit()
        except sqlite3.Error as e:
            log.warning("convo store unavailable (%s); follow-up memory disabled", e)
            self.con = None

    def record(self, msg_id: int, parent_id: int | None, question: str, answer: str) -> None:
        if self.con is None:
            return
        now = time.time()
        try:
            self.con.execute(
                "INSERT OR REPLACE INTO convo VALUES (?,?,?,?,?)",
                (msg_id, parent_id, question[:300], answer[:600], now),
            )
            self.con.execute("DELETE FROM convo WHERE ts < ?", (now - self.KEEP_DAYS * 86400,))
            self.con.commit()
        except sqlite3.Error as e:
            log.warning("convo store write failed: %s", e)

    def chain(self, msg_id: int, limit: int = 3) -> list[tuple[str, str]]:
        """Walk parent links from msg_id; oldest exchange first."""
        if self.con is None:
            return []
        out: list[tuple[str, str]] = []
        cur: int | None = msg_id
        while cur is not None and len(out) < limit:
            row = self.con.execute(
                "SELECT parent_id, question, answer FROM convo WHERE msg_id = ?", (cur,)
            ).fetchone()
            if row is None:
                break
            out.append((row[1], row[2]))
            cur = row[0]
        return list(reversed(out))


class SnowyBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # required for the OOC + ahelp reply bridges
        intents.members = True          # required to count staff for /ask Discord answers
        super().__init__(command_prefix="!unused!", intents=intents)
        self._web_runner: web.AppRunner | None = None
        # ahelp ticket bridge state (in-memory; rebuilt as new tickets arrive after a restart)
        self.ticket_threads: dict[str, int] = {}   # initiator ckey -> thread id
        self.thread_ckeys: dict[int, str] = {}      # thread id -> initiator ckey
        # RAG /ask: tiny local LLM answering strictly from the game knowledge base.
        self.rag = RagAnswerer(
            config.RAG_DB, config.EMBED_URL, config.LLM_URL,
            top_k=config.RAG_TOP_K, min_score=config.RAG_MIN_SCORE,
        )
        self._ask_lock = asyncio.Lock()             # one CPU inference at a time
        self._ask_last: dict[int, float] = {}       # user id -> monotonic timestamp
        self._ask_waiting = 0                       # requests queued behind the lock
        self._discord_docs_indexed = False          # channel directory indexed once
        self.convo = ConvoStore(config.CONVO_DB)    # reply-chain follow-up memory
        self._last_quill_ooc = 0.0                  # monotonic ts of last in-game Quill OOC answer

    async def setup_hook(self) -> None:
        await self._start_ingest_server()
        if config.GUILD_ID:
            guild = discord.Object(id=config.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, getattr(self.user, "id", "?"))
        if not self.presence_loop.is_running():
            self.presence_loop.start()
        if not self._discord_docs_indexed:
            self._discord_docs_indexed = True
            await self._index_discord_docs()

    # --- Discord server knowledge for /ask --------------------------------

    def _ask_guild(self) -> discord.Guild | None:
        return self.get_guild(config.GUILD_ID) if config.GUILD_ID else None

    async def _index_discord_docs(self) -> None:
        """Index channel directory entries so /ask can answer 'where do I...'.
        Only channels visible to @everyone — private/staff channel names and
        topics must not leak through answers."""
        guild = self._ask_guild()
        if guild is None:
            return
        docs: list[tuple[str, str, str]] = []
        for ch in [*guild.text_channels, *guild.forums]:
            if not ch.permissions_for(guild.default_role).view_channel:
                continue
            topic = (ch.topic or "").strip()
            text = f"#{ch.name} is a channel on the Snowywood Discord server."
            if topic:
                text += f" Its topic says: {topic}"
            docs.append(("discord", f"#{ch.name}", text))
        added = await self.rag.add_documents(docs)
        log.info("Discord channel directory: indexed %d/%d channels", added, len(docs))

    def discord_live_facts(self) -> str | None:
        """Tier-1 live facts about the Discord server for the /ask context."""
        guild = self._ask_guild()
        if guild is None:
            return None
        mods = [
            m.display_name for m in guild.members
            if not m.bot and (m.guild_permissions.kick_members or m.guild_permissions.administrator)
        ]
        # Member cache needs the (privileged) members intent; don't report a
        # bogus zero if chunking failed or the intent is missing.
        mod_part = ""
        if mods:
            mod_part = f" Its {len(mods)} moderators/staff are: {', '.join(sorted(mods)[:20])}."
        return f"The Discord server '{guild.name}' has {guild.member_count} members.{mod_part}"

    # --- bot -> game helpers -------------------------------------------------

    async def game_query(self, keyword: str, value: str | None = None, **params: str):
        return await world_topic(
            config.GAME_HOST,
            config.GAME_PORT,
            build_query(keyword, value, key=config.GAME_COMMS_KEY, **params),
        )

    # --- presence ------------------------------------------------------------

    @tasks.loop(seconds=config.PRESENCE_INTERVAL)
    async def presence_loop(self) -> None:
        status = await get_status(config.GAME_HOST, config.GAME_PORT, config.GAME_COMMS_KEY)
        if not status:
            activity = discord.Activity(type=discord.ActivityType.watching, name="server offline")
        else:
            players = status.get("players", "?")
            secs = int(float(status.get("round_duration", 0) or 0))
            label = f"{players} players | {secs // 60}m" if secs else f"{players} players"
            activity = discord.Activity(type=discord.ActivityType.watching, name=label)
        await self.change_presence(activity=activity)

    @presence_loop.before_loop
    async def _before_presence(self) -> None:
        await self.wait_until_ready()

    # --- game -> bot ingest server ------------------------------------------

    async def _start_ingest_server(self) -> None:
        app = web.Application()
        app.router.add_post("/ingest", self._handle_ingest)
        app.router.add_get("/health", lambda _r: web.Response(text="ok"))
        self._web_runner = web.AppRunner(app)
        await self._web_runner.setup()
        site = web.TCPSite(self._web_runner, config.INGEST_HOST, config.INGEST_PORT)
        await site.start()
        log.info("Ingest server listening on %s:%s", config.INGEST_HOST, config.INGEST_PORT)

    async def _handle_ingest(self, request: web.Request) -> web.Response:
        if request.headers.get("X-Bot-Secret") != config.INGEST_SECRET:
            return web.Response(status=401, text="bad secret")
        try:
            payload = await request.json()
        except Exception:
            return web.Response(status=400, text="bad json")

        etype = payload.get("type")
        try:
            if etype == "round":
                await self._on_round(payload)
            elif etype == "ooc":
                await self._on_ooc(payload)
            elif etype == "ahelp":
                await self._on_ahelp(payload)
            else:
                return web.Response(status=400, text="unknown type")
        except Exception:
            log.exception("Failed handling ingest event %s", etype)
            return web.Response(status=500, text="error")
        return web.Response(text="ok")

    async def _on_round(self, payload: dict) -> None:
        channel = self.get_channel(config.ANNOUNCE_CHANNEL_ID)
        if not isinstance(channel, discord.abc.Messageable):
            return
        event = payload.get("event", "")
        title, color = {
            "start": ("Round starting", discord.Color.green()),
            "ending": ("Round ending", discord.Color.orange()),
            "end": ("Round ended", discord.Color.red()),
        }.get(event, (f"Round: {event}", discord.Color.blurple()))
        embed = discord.Embed(title=title, color=color)
        if payload.get("map"):
            embed.add_field(name="Map", value=str(payload["map"]))
        await channel.send(embed=embed)

    async def _on_ooc(self, payload: dict) -> None:
        message = str(payload.get("message", ""))
        channel = self.get_channel(config.OOC_CHANNEL_ID)
        if isinstance(channel, discord.abc.Messageable):
            sender = discord.utils.escape_markdown(str(payload.get("sender", "?")))
            await channel.send(f"**{sender}:** {message}", allowed_mentions=discord.AllowedMentions.none())
        # In-game Quill: an OOC mention of "quill" gets a RAG answer spoken back
        # in-game. Fire-and-forget so the game's /ingest POST returns immediately
        # (RAG inference takes a few seconds).
        if self.rag.ok and QUILL_OOC_TRIGGER.search(message):
            asyncio.create_task(self._answer_ooc_quill(message))

    async def _answer_ooc_quill(self, message: str) -> None:
        """Answer an in-game OOC mention of 'quill' via the RAG and broadcast the
        reply back in-game through the quill_say world topic."""
        now = time.monotonic()
        if now - self._last_quill_ooc < QUILL_OOC_COOLDOWN:
            return
        # Strip the trigger word to isolate the actual question.
        question = QUILL_OOC_TRIGGER.sub(" ", message).strip(" ,.:;?!").strip()
        if len(question) < 3:
            return
        self._last_quill_ooc = now  # set before awaiting (anti-spam) — single-threaded, no race
        try:
            async with self._ask_lock:  # serialize with Discord /ask — one inference at a time
                answer, _sources = await self.rag.answer(question)
            if not answer:
                return
            answer = " ".join(answer.split())[:350]  # collapse whitespace + cap for the world topic
            await self.game_query("quill_say", message=answer)
        except Exception:
            log.exception("Failed answering in-game Quill OOC mention")

    async def _on_ahelp(self, payload: dict) -> None:
        channel = self.get_channel(config.AHELP_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.fetch_channel(config.AHELP_CHANNEL_ID)
            except discord.HTTPException as e:
                log.warning("Cannot access ahelp channel %s: %s", config.AHELP_CHANNEL_ID, e)
                return
        if not isinstance(channel, discord.TextChannel):
            log.warning("Ahelp channel %s is not a TextChannel (got %s)", config.AHELP_CHANNEL_ID, type(channel))
            return
        ckey = str(payload.get("sender", "unknown"))
        ticket_id = str(payload.get("id", "?"))
        message = str(payload.get("message", ""))

        thread = None
        existing = self.ticket_threads.get(ckey)
        if existing:
            thread = channel.get_thread(existing) or self.get_channel(existing)
        if thread is None:
            thread = await channel.create_thread(
                name=f"Ticket #{ticket_id} - {ckey}"[:100],
                type=discord.ChannelType.public_thread,
            )
            self.ticket_threads[ckey] = thread.id
            self.thread_ckeys[thread.id] = ckey
            log.info("Opened ahelp thread %s for ckey %s", thread.id, ckey)
        await thread.send(
            f"**{discord.utils.escape_markdown(ckey)}:** {message}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # --- @mention / reply ask ------------------------------------------------

    def _explicitly_mentioned(self, message: discord.Message) -> bool:
        return self.user is not None and (
            f"<@{self.user.id}>" in message.content or f"<@!{self.user.id}>" in message.content
        )

    def _replied_bot_message(self, message: discord.Message) -> discord.Message | None:
        """The bot message this one replies to, if any (gateway-resolved only)."""
        ref = message.reference.resolved if message.reference else None
        if isinstance(ref, discord.Message) and ref.author == self.user:
            return ref
        return None

    async def handle_ask_message(self, message: discord.Message, replied: discord.Message | None) -> None:
        question = re.sub(rf"<@!?{self.user.id}>", "", message.content).strip()
        if len(question) < 5:
            await message.reply(
                "Ask me a question about the game, e.g. `what does the Bishop do?`",
                mention_author=False,
            )
            return
        now = time.monotonic()
        last = self._ask_last.get(message.author.id, 0.0)
        if now - last < config.ASK_COOLDOWN:
            await message.reply(
                f"Patience, dear traveler. Another {int(config.ASK_COOLDOWN - (now - last)) + 1}s before you may ask again.",
                mention_author=False,
            )
            return
        self._ask_last[message.author.id] = now

        # Replying to an /ask answer makes the whole reply chain (up to 3
        # exchanges) follow-up context. Fall back to the replied embed itself
        # for answers that predate the convo store.
        history = None
        if replied:
            history = self.convo.chain(replied.id) or None
            if history is None and replied.embeds:
                e = replied.embeds[0]
                if e.title and e.description:
                    history = [(str(e.title), str(e.description)[:600])]

        live, count, names, facts = await build_live_data()
        answer = presence_answer(question, count, names, facts)
        sources = ["Live server status"] if answer is not None else []
        if answer is None:
            if self._ask_waiting >= config.ASK_MAX_QUEUE:
                await message.reply(
                    "Oh my, several seekers await my counsel. Do return in a minute, dear traveler.",
                    mention_author=False,
                )
                return
            self._ask_waiting += 1
            try:
                async with message.channel.typing():
                    async with self._ask_lock:
                        answer, sources = await self.rag.answer(
                            question, live_context=live, history=history
                        )
            finally:
                self._ask_waiting -= 1
        sent = await message.reply(format_answer(answer, sources), mention_author=False)
        self.convo.record(sent.id, replied.id if replied else None, question, answer)

    # --- Discord -> game bridge (messages) ----------------------------------

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.content:
            return

        # Reply inside an ahelp ticket thread -> PM the player in game
        ckey = self.thread_ckeys.get(message.channel.id)
        if ckey:
            await self.game_query(
                "adminmsg",
                value=ckey,
                msg=message.content,
                sender=message.author.display_name,
            )
            return

        mentioned = self._explicitly_mentioned(message)
        is_ooc = message.channel.id == config.OOC_CHANNEL_ID

        # @Snowywood <question> anywhere, or replying to one of its /ask
        # answers (outside OOC, where replies are normal conversation).
        if self.rag.ok and (mentioned or (not is_ooc and self._replied_bot_message(message))):
            log.info("ask via %s from %s in #%s: %s",
                     "mention" if mentioned else "reply", message.author,
                     getattr(message.channel, "name", message.channel.id),
                     message.content[:120])
            await self.handle_ask_message(message, self._replied_bot_message(message))
            return

        # OOC channel -> in-game OOC
        if is_ooc:
            await self.game_query(
                "discord_ooc",
                sender=message.author.display_name,
                message=message.content,
            )


bot = SnowyBot()


@bot.tree.command(name="players", description="Show the current player count.")
async def players_cmd(interaction: discord.Interaction) -> None:
    count = await bot.game_query("playing")
    if count is None:
        await interaction.response.send_message("Server is not responding.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Players online: **{int(count)}**")


def format_answer(answer: str, sources: list[str]) -> str:
    """Plain-message answer with a small-text source line (no embed)."""
    text = answer[:1800]
    if sources:
        text += "\n-# Sources: " + ", ".join(sources)[:150]
    return text


async def build_live_data() -> tuple[str | None, int, str, dict]:
    """Real-time server facts for /ask.
    Returns (context block, player count, names, structured facts)."""
    status = await get_status(config.GAME_HOST, config.GAME_PORT, config.GAME_COMMS_KEY)
    if not status:
        facts = bot.discord_live_facts()
        block = f"The game server is offline or unreachable right now. {facts}" if facts else None
        return block, -1, "", {}
    names = ""
    if config.ASK_PLAYER_NAMES:
        names_raw = await bot.game_query("playerlist")
        names = names_raw.strip() if isinstance(names_raw, str) else ""
    try:
        count = int(float(status.get("players", 0) or 0))
    except ValueError:
        count = -1
    if names:
        players = f"{count} player(s) connected: {names}"
    elif count == 0:
        players = "no players connected"
    else:
        players = f"{count} player(s) connected (names unavailable)"
    secs = int(float(status.get("round_duration", 0) or 0))
    block = (
        f"{players}. Current map: {status.get('map_name', '?')}. "
        f"Round ID: {status.get('round_id', '?')}. "
        f"Round has been going for {secs // 60} minutes. "
        f"Staff (admins) online in game: {status.get('admins', '?')}."
    )
    discord_facts = bot.discord_live_facts()
    if discord_facts:
        block += f" {discord_facts}"
    facts = {"map": status.get("map_name"), "minutes": secs // 60}
    return block, count, names, facts


# Live-status questions with structured answers (who's online, current map,
# round time) are answered deterministically: the small model mimics worked
# examples but ignores abstract rules, and grounding on these regressed every
# time the prompt changed. Code can't hallucinate.
PRESENCE_RE = re.compile(
    r"\b(who'?s?\s+(is\s+)?(online|on|playing)|anyone\s+(online|on|playing)"
    r"|is\s+\S+\s+(online|playing|on\s+the\s+server|connected)"
    r"|how\s+many\s+(players|people)\b)",
    re.IGNORECASE,
)
MAP_RE = re.compile(
    r"\b(what|which|current)\b.{0,30}\bmap\b|\bmap\s+(is|are)\s+(it|on|the\s+server)\b",
    re.IGNORECASE,
)
ROUNDTIME_RE = re.compile(
    r"\bround\b.{0,30}\b(time|long|going|duration|started)\b"
    r"|\b(how\s+long|when)\b.{0,30}\bround\b",
    re.IGNORECASE,
)


IDENTITY_RE = re.compile(
    r"\b(who|what)\s+(are|r)\s+(you|u)\b|\byour\s+name\b|\bwho\s+(is|are)\s+(quill|this\s+bot)\b",
    re.IGNORECASE,
)
CREATOR_RE = re.compile(
    r"\bwho\s+(made|created|built|wrote|developed|coded|programmed)\s+(you|u|quill|this\s+bot)\b"
    r"|\byour\s+(creator|maker|developer|author)\b",
    re.IGNORECASE,
)


def presence_answer(question: str, count: int, names: str, facts: dict) -> str | None:
    """Deterministic reply for live-status and identity questions; None to use the LLM."""
    if CREATOR_RE.search(question):
        return ("I was penned into being by Mooshieblob, keeper of this realm! "
                "You may find more of their workings at github.com/Mooshieblob1, dear traveler.")
    if IDENTITY_RE.search(question):
        return ("I am Quill, dear traveler! A humble scribe and devotee of Noc, keeper of "
                "Snowywood's tomes. Ask me of the realm's jobs, gods, races, recipes or lore "
                "and I shall consult my pages for you.")
    if count < 0:
        return None
    if PRESENCE_RE.search(question):
        if count == 0:
            return "The realm stands empty right now, dear traveler. Not a soul about!"
        souls = "soul wanders" if count == 1 else "souls wander"
        if names:
            return f"Oh! {count} {souls} the realm at present: {names}."
        return f"{count} {souls} the realm at present."
    if MAP_RE.search(question) and facts.get("map"):
        return f"The realm rests upon {facts['map']} at present, dear traveler."
    if ROUNDTIME_RE.search(question) and facts.get("minutes") is not None:
        m = facts["minutes"]
        return f"This tale has been unfolding for {m} minute{'s' if m != 1 else ''} now."
    return None


@bot.tree.command(name="ask", description="Ask a question about the game (jobs, gods, recipes, mechanics).")
@app_commands.describe(question="Your question about Snowywood")
async def ask_cmd(interaction: discord.Interaction, question: app_commands.Range[str, 5, 300]) -> None:
    if not bot.rag.ok:
        await interaction.response.send_message(
            "The game knowledge base isn't available right now.", ephemeral=True
        )
        return
    now = time.monotonic()
    last = bot._ask_last.get(interaction.user.id, 0.0)
    if now - last < config.ASK_COOLDOWN:
        await interaction.response.send_message(
            f"Patience, dear traveler. Another {int(config.ASK_COOLDOWN - (now - last)) + 1}s before you may ask again.",
            ephemeral=True,
        )
        return
    bot._ask_last[interaction.user.id] = now

    await interaction.response.defer()  # CPU inference takes a few seconds
    live, count, names, facts = await build_live_data()
    answer = presence_answer(question, count, names, facts)
    if answer is not None:
        sources = ["Live server status"]
    else:
        # Cap the inference queue: with one slot at ~10s/answer, more than a
        # few waiters means minute-plus waits — better to bounce immediately.
        if bot._ask_waiting >= config.ASK_MAX_QUEUE:
            await interaction.followup.send(
                "Oh my, several seekers await my counsel. Do return in a minute, dear traveler.",
                ephemeral=True,
            )
            return
        bot._ask_waiting += 1
        try:
            async with bot._ask_lock:
                answer, sources = await bot.rag.answer(question, live_context=live)
        finally:
            bot._ask_waiting -= 1
    # Quote the question (slash invocations don't show the option text),
    # then answer as a plain message — embeds are kept for /status etc.
    content = f"> {question[:250]}\n{format_answer(answer, sources)}"
    sent = await interaction.followup.send(content[:2000], wait=True)
    bot.convo.record(sent.id, None, question, answer)


@bot.tree.command(name="status", description="Show live server status.")
async def status_cmd(interaction: discord.Interaction) -> None:
    status = await get_status(config.GAME_HOST, config.GAME_PORT, config.GAME_COMMS_KEY)
    if not status:
        await interaction.response.send_message("Server is not responding.", ephemeral=True)
        return
    secs = int(float(status.get("round_duration", 0) or 0))
    embed = discord.Embed(title="Server status", color=discord.Color.green())
    embed.add_field(name="Players", value=status.get("players", "?"))
    embed.add_field(name="Map", value=status.get("map_name", "?"))
    embed.add_field(name="Round", value=status.get("round_id", "?"))
    embed.add_field(name="Round time", value=f"{secs // 60}m {secs % 60}s")
    embed.add_field(name="Admins", value=status.get("admins", "?"))
    embed.add_field(name="Gamestate", value=status.get("gamestate", "?"))
    await interaction.response.send_message(embed=embed)


if __name__ == "__main__":
    bot.run(config.TOKEN, log_handler=None)
