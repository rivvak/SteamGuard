"""
SteamGuard Discord Bot — Full Version v2
──────────────────────────────────────────
Channel rules:
  • All commands work in any channel (slash commands) or anywhere in the server
  • DMs get a friendly redirect message with server invite link

User commands (in #get-key only):
  !getkey      — issues your license key via DM (requires Member role)
  !mykey       — shows your key status, how long it's been running, verify count
  !linkyoutube — starts YouTube OAuth flow so we can verify your subscription

Admin commands (in any channel, admin only):
  !listkeys [all|active|paused|revoked]  — table of all keys + stats
  !keyinfo @user                         — all keys for a specific user
  !pausekey @user [reason]               — suspend a user's key immediately
  !unpausekey @user                      — resume a suspended key
  !revokekey @user [reason]              — permanently revoke
  !sgstatus                              — bot + server health check (with server stats)

Slash commands:
  /status       — Check your SteamGuard license and account status (ephemeral)
  /refer        — Get your personal referral link (ephemeral)
  /stats        — View your SteamGuard protection statistics (public)
  /leaderboard  — View the weekly leaderboard (public)
  /reset-device — Self-service HWID reset with confirmation (ephemeral)
  /download     — Get the latest installer link (ephemeral)
  /vote         — Vote on upcoming features (public)
  /support      — Submit a support request via modal (ephemeral)
  /checkbadges  — Check for newly earned badges and announce them

Auto-tasks:
  Every 24 h : membership sweep — pauses keys for anyone who lost the role
  Every 10 m : stat channel name updates (Members / Active / Heals)

Required env vars:
  DISCORD_BOT_TOKEN   — bot token
  DISCORD_GUILD_ID    — your server ID
  DISCORD_ROLE_ID     — "Member" role ID
  GETKEY_CHANNEL_ID   — channel ID where #get-key lives
  LICENSE_SERVER_URL  — https://your-cloud-run-url
  ADMIN_KEY           — matches server ADMIN_KEY secret
  ADMIN_USER_IDS      — comma-separated Discord user IDs with admin access
  DISCORD_INVITE      — your server invite link (e.g. discord.gg/xxxx)
  YOUTUBE_CLIENT_ID   — (optional) Google OAuth client ID for YT linking
  YOUTUBE_CLIENT_SECRET — (optional) Google OAuth client secret

New optional env vars:
  SUPPORT_CHANNEL_ID  — channel to post support requests
  UPDATE_DOWNLOAD_URL — override download URL (default: GitHub releases)
  VOTE_TOPICS         — JSON array string of vote topics (optional)
  BADGE_ANNOUNCE_CHANNEL_ID — channel to announce badge unlocks
"""

import os
import json
import asyncio
import logging
import time
import sys
import importlib.util
import hmac
import hashlib
import httpx
import discord
import discord.app_commands
from discord.ext import commands, tasks
from datetime import datetime, timezone
from pathlib import Path

LOG = logging.getLogger("sg-bot")
logging.basicConfig(level=logging.INFO)

# Ensure cog imports resolve when bot.py is executed as a script in containers.
_COGS_ROOT = Path(__file__).resolve().parent / "bot"
if _COGS_ROOT.is_dir():
    _cogs_path = str(_COGS_ROOT)
    if _cogs_path not in sys.path:
        sys.path.insert(0, _cogs_path)

_COGS_DIR = _COGS_ROOT / "cogs"


def _load_cog_setup(module_file: str):
    mod_path = _COGS_DIR / module_file
    if not mod_path.is_file():
        raise ImportError(f"Cog module file not found: {mod_path}")
    mod_name = f"steamguard_cog_{module_file.replace('.py', '')}"
    spec = importlib.util.spec_from_file_location(mod_name, mod_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec: {mod_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    setup = getattr(module, "setup", None)
    if setup is None:
        raise ImportError(f"Cog module missing setup(): {mod_path}")
    return setup

# ── Env vars (original) ───────────────────────────────────────────────────────

GUILD_ID           = int(os.environ["DISCORD_GUILD_ID"])
ROLE_ID            = int(os.environ["DISCORD_ROLE_ID"])
GETKEY_CHANNEL_ID    = int(os.environ.get("GETKEY_CHANNEL_ID", "0"))
OFF_TOPIC_CHANNEL_ID = int(os.environ.get("OFF_TOPIC_CHANNEL_ID", "1513193890117714182"))
MOD_CHANNEL_ID       = int(os.environ.get("MOD_CHANNEL_ID", "1513296463197769770"))  # moderator channel for admin alerts
LICENSE_SERVER_URL = os.environ["LICENSE_SERVER_URL"]
ADMIN_KEY          = os.environ["ADMIN_KEY"]
# Shared HMAC secret — must match the license server's SECRET_KEY so that
# request signatures (e.g. /device/reset) validate via _verify_sig.
SECRET_KEY         = os.environ.get("SECRET_KEY", "")
BOT_TOKEN          = os.environ["DISCORD_BOT_TOKEN"]

def _hmac_sign(data: str) -> str:
    """Mirror of the license server's _hmac_sign — HMAC-SHA256 over SECRET_KEY."""
    return hmac.new(SECRET_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()
DISCORD_INVITE     = os.environ.get("DISCORD_INVITE", "https://discord.gg/REPLACE")
# Optional: channel to mint per-user referral invites in. Falls back to the
# guild's system channel, then the first accessible text channel.
REFERRAL_INVITE_CHANNEL_ID = int(os.environ.get("REFERRAL_INVITE_CHANNEL_ID", "0") or "0")

# ── Referral invite-attribution cache ────────────────────────────────────────
# Maps invite.code -> invite.uses for the configured guild. Rebuilt on
# on_ready and kept in sync via on_invite_create / on_invite_delete /
# on_member_join (diffing use counts to identify which invite was used).
_invite_uses: dict[str, int] = {}

ADMIN_USER_IDS: set[int] = set(
    int(x) for x in os.environ.get("ADMIN_USER_IDS", "").split(",") if x.strip()
)

# Owner Discord IDs — exempt from reward tracking, get unlimited time
OWNER_DISCORD_IDS: set[int] = set(
    int(x) for x in os.environ.get("ADMIN_USER_IDS", "").split(",") if x.strip()
)

YOUTUBE_URL  = "https://www.youtube.com/@Rivvak"

# Human-readable reward menu (trigger -> display info)
REWARD_MENU = {
    "invite_friend":   ("Invite a friend",             "+3h",  "Friend joins server & activates SteamGuard"),
    "daily_check_in":  ("Daily check-in",               "+30m", "Run SteamGuard and check in once per day"),
    "weekly_streak":   ("7-day protection streak",      "+5h",  "Protect for 7 days in a row"),
    "youtube_sub":     ("Subscribe on YouTube",         "+2h",  "Subscribe to youtube.com/@Rivvak (once ever)"),
    "server_boost":    ("Boost the Discord server",     "+24h", "Boost the Rivvak Community server (once ever)"),
    "share_card_post": ("Share your stats card",        "+1h",  "Post your SteamGuard stats card in #showcase"),
    "bug_report":      ("Report a verified bug",        "+6h",  "Submit a bug via /support — admin verifies"),
    "first_heal":      ("First protection heal",        "+1h",  "Earn your first heal (once ever)"),
}

YT_CLIENT_ID     = os.environ.get("YOUTUBE_CLIENT_ID", "")
YT_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET", "")

# ── Env vars (new) ────────────────────────────────────────────────────────────

SUPPORT_CHANNEL_ID          = int(os.environ.get("SUPPORT_CHANNEL_ID", "0") or "0")
BADGE_ANNOUNCE_CHANNEL_ID   = int(os.environ.get("BADGE_ANNOUNCE_CHANNEL_ID", "0") or "0")
UPDATE_DOWNLOAD_URL         = os.environ.get(
    "UPDATE_DOWNLOAD_URL", "https://link-center.net/663392/XdEl0GT9TuQv"
)
_VOTE_TOPICS_RAW = os.environ.get("VOTE_TOPICS", "")
DEFAULT_VOTE_TOPICS = [
    "Cloud settings backup",
    "Multi-device support (2 PCs)",
    "Monthly recap card",
    "Custom themes",
    "Playtime tracker",
]
try:
    VOTE_TOPICS: list[str] = json.loads(_VOTE_TOPICS_RAW) if _VOTE_TOPICS_RAW else DEFAULT_VOTE_TOPICS
except Exception:
    VOTE_TOPICS = DEFAULT_VOTE_TOPICS

# ── Colours ───────────────────────────────────────────────────────────────────

C_BLUE   = 0x2563eb
C_GREEN  = 0x22c55e
C_RED    = 0xef4444
C_YELLOW = 0xf59e0b
C_GREY   = 0x64748b

# ── Embed factory ─────────────────────────────────────────────────────────────
# AuthGuard brand palette
_C_BRAND   = 0x7C5CFC   # Default/neutral embeds — purple brand
_C_SUCCESS = 0x22D3A5   # Key redeemed, reward claimed — teal
_C_ERROR   = 0xEF4444   # Failed redemption, invalid key
_C_WARN    = 0xF59E0B   # Cooldowns, confirmations — amber
_C_INFO    = 0x5865F2   # Help, status, neutral info — Discord blurple
_C_PURPLE  = 0x7C5CFC   # Rivvak brand purple

def _embed(title: str, description: str = "", color: int = _C_BRAND,
           fields: list = None, footer: str = None) -> discord.Embed:
    """Base embed with consistent AuthGuard branding."""
    e = discord.Embed(title=title, description=description, color=color)
    e.set_author(name="AuthGuard • Rivvak Community", icon_url="https://i.imgur.com/5RHR6Xa.png")
    e.set_footer(text=f"AuthGuard • Rivvak Community  |  rivvak.app{(' | ' + footer) if footer else ''}")
    e.timestamp = discord.utils.utcnow()
    if fields:
        for f in fields:
            e.add_field(name=f.get("name","\u200b"), value=f.get("value","\u200b"),
                        inline=f.get("inline", True))
    return e

def _embed_success(title: str, description: str = "", **kwargs) -> discord.Embed:
    return _embed(f"\u2705  {title}", description, color=_C_SUCCESS, **kwargs)

def _embed_error(title: str, description: str = "", **kwargs) -> discord.Embed:
    return _embed(f"\u26d4  {title}", description, color=_C_ERROR, **kwargs)

def _embed_warn(title: str, description: str = "", **kwargs) -> discord.Embed:
    return _embed(f"\u26a0\ufe0f  {title}", description, color=_C_WARN, **kwargs)

def _embed_info(title: str, description: str = "", **kwargs) -> discord.Embed:
    return _embed(f"\u2139\ufe0f  {title}", description, color=_C_INFO, **kwargs)

# ── Bot setup ─────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.members         = True
intents.message_content = True


class SteamGuardBot(commands.Bot):
    """SteamGuard bot subclass.

    setup_hook fires exactly once before on_ready, so it's the right place
    to load cogs — on_ready can fire multiple times (resume/reconnect) and
    reloading cogs there would raise. The cog-registration blocks below
    mirror the original on_ready loading exactly.
    """

    async def setup_hook(self) -> None:
        # ── Register AI /ask cog (Phase 1) ──
        # Wrapped so a missing dep never blocks the rest of setup.
        if os.environ.get("AI_ASK_ENABLED", "false").lower() == "true":
            try:
                setup_ask = _load_cog_setup("ask_cog.py")
                await setup_ask(self)
                LOG.info("Registered /ask cog")
            except Exception as e:
                LOG.warning(f"Failed to register /ask cog: {e}")
        else:
            LOG.info("AI_ASK_ENABLED=false — /ask cog not registered")

        # ── Register AI /develop cog (Phase 2) ──
        if os.environ.get("AI_DEVELOP_ENABLED", "false").lower() == "true":
            try:
                setup_develop = _load_cog_setup("develop_cog.py")
                await setup_develop(self)
                LOG.info("Registered /develop cog")
            except Exception as e:
                LOG.warning(f"Failed to register /develop cog: {e}")
        else:
            LOG.info("AI_DEVELOP_ENABLED=false — /develop cog not registered")

        # ── Register AI /create cog (Phase 3) ──
        if os.environ.get("AI_CREATE_ENABLED", "false").lower() == "true":
            try:
                setup_create = _load_cog_setup("create_cog.py")
                await setup_create(self)
                LOG.info("Registered /create cog")
            except Exception as e:
                LOG.warning(f"Failed to register /create cog: {e}")
        else:
            LOG.info("AI_CREATE_ENABLED=false — /create cog not registered")

        # ── Register /memory cog (Phase 4) ──
        # Only makes sense when at least one of /create or /develop is enabled;
        # otherwise there's nothing writing to memory. We gate on either.
        if (
            os.environ.get("AI_CREATE_ENABLED", "false").lower() == "true"
            or os.environ.get("AI_DEVELOP_ENABLED", "false").lower() == "true"
        ):
            try:
                setup_memory = _load_cog_setup("memory_cog.py")
                await setup_memory(self)
                LOG.info("Registered /memory cog (Phase 4)")
            except Exception as e:
                LOG.warning(f"Failed to register /memory cog: {e}")
        else:
            LOG.info("AI_CREATE_ENABLED and AI_DEVELOP_ENABLED both false — /memory cog not registered")


bot = SteamGuardBot(command_prefix="!", intents=intents)


def in_getkey_channel():
    """!getkey / !mykey / !linkyoutube — only in #get-key channel."""
    async def predicate(ctx: commands.Context):
        if ctx.author.id in ADMIN_USER_IDS:
            return True   # admins bypass everywhere
        if ctx.guild is None:
            return False
        if GETKEY_CHANNEL_ID == 0:
            return True
        return ctx.channel.id == GETKEY_CHANNEL_ID
    return commands.check(predicate)

def in_off_topic():
    """All general commands — only in #-off-topic channel.
    Admins are exempt and can run commands anywhere."""
    async def predicate(ctx: commands.Context):
        if ctx.author.id in ADMIN_USER_IDS:
            return True
        if ctx.guild and ctx.author.guild_permissions.administrator:
            return True
        if ctx.guild is None:
            return False
        if OFF_TOPIC_CHANNEL_ID == 0:
            return True
        return ctx.channel.id == OFF_TOPIC_CHANNEL_ID
    return commands.check(predicate)

def is_admin():
    async def predicate(ctx: commands.Context):
        if ctx.author.id in ADMIN_USER_IDS:
            return True
        if ctx.guild and ctx.author.guild_permissions.administrator:
            return True
        return False
    return commands.check(predicate)

# ── API helpers ───────────────────────────────────────────────────────────────

async def _api(method: str, path: str, **kwargs) -> dict:
    async with httpx.AsyncClient(timeout=12) as client:
        r = await getattr(client, method)(
            f"{LICENSE_SERVER_URL}{path}",
            headers={"x-admin-key": ADMIN_KEY},
            **kwargs)
        if r.status_code == 200:
            return r.json()
        return {"error": r.text, "status_code": r.status_code}

async def _api_get(path: str, **params) -> dict:
    async with httpx.AsyncClient(timeout=12) as client:
        r = await client.get(
            f"{LICENSE_SERVER_URL}{path}",
            headers={"x-admin-key": ADMIN_KEY},
            params=params)
        if r.status_code == 200:
            return r.json()
        return {"error": r.text, "status_code": r.status_code}

# ── DM interception ───────────────────────────────────────────────────────────

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Redirect DMs to server
    if message.guild is None:
        embed = _embed_warn(
            "I don't accept DMs",
            (
                "All SteamGuard commands must be used in our Discord server.\n\n"
                f"👉  **[Click here to join]({DISCORD_INVITE})**\n\n"
                "Then use `!getkey` in the **#get-key** channel."
            ))
        try:
            await message.channel.send(embed=embed)
        except Exception:
            pass
        return

    # ── Channel routing for prefix commands ──────────────────────────────────
    if message.content.startswith("!") and message.guild is not None:
        is_admin_user = (message.author.id in ADMIN_USER_IDS
                         or message.author.guild_permissions.administrator)
        if not is_admin_user:
            key_cmds = {"!getkey", "!mykey", "!linkyoutube"}
            cmd_word = message.content.split()[0].lower()

            if cmd_word in key_cmds:
                # Must be in #get-key
                if GETKEY_CHANNEL_ID != 0 and message.channel.id != GETKEY_CHANNEL_ID:
                    ch = message.guild.get_channel(GETKEY_CHANNEL_ID)
                    ref = ch.mention if ch else "#get-key"
                    await message.reply(f"❌ `{cmd_word}` only works in {ref}.", delete_after=8)
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    return
            else:
                # All other commands must be in #-off-topic
                if OFF_TOPIC_CHANNEL_ID != 0 and message.channel.id != OFF_TOPIC_CHANNEL_ID:
                    ch = message.guild.get_channel(OFF_TOPIC_CHANNEL_ID)
                    ref = ch.mention if ch else "#-off-topic"
                    await message.reply(f"❌ Please use commands in {ref}.", delete_after=8)
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    return

    await bot.process_commands(message)

# ── Events ────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    LOG.info(f"Bot ready: {bot.user} (ID {bot.user.id})")
    await _post_welcome_embed()
    daily_membership_sweep.start()
    morning_health_check.start()
    uptime_check.start()
    weekly_key_audit.start()
    weekly_key_cleanup.start()
    weekly_gen_audit.start()

    # Sync slash commands to the guild
    try:
        guild_obj = discord.Object(id=GUILD_ID)
        synced = await bot.tree.sync(guild=guild_obj)
        LOG.info(f"Synced {len(synced)} slash command(s) to guild {GUILD_ID}")
    except Exception as e:
        LOG.error(f"Failed to sync slash commands: {e}")

    # Build the invite-uses cache for referral attribution. Reading a
    # guild's invites requires the Manage Server (Manage Guild) permission —
    # if the bot lacks it, referral attribution via on_member_join simply
    # won't work, so warn loudly instead of failing silently.
    try:
        guild = bot.get_guild(GUILD_ID)
        if guild is not None:
            invites = await guild.invites()
            _invite_uses.clear()
            _invite_uses.update({inv.code: (inv.uses or 0) for inv in invites})
            LOG.info(f"Cached {len(_invite_uses)} invite(s) for referral attribution")
        else:
            LOG.warning(f"Could not find guild {GUILD_ID} to cache invites")
    except discord.Forbidden:
        LOG.warning(
            "Missing 'Manage Server' permission — cannot list guild invites, "
            "so referral attribution via Discord invites will not work.")
    except Exception as e:
        LOG.warning(f"Failed to build invite cache: {e}")

    # Fetch total license count for presence display
    total_keys = 0
    try:
        _stats = await _api_get("/stats/server")
        total_keys = _stats.get("total_keys", 0) or 0
    except Exception:
        total_keys = 0

    # Rotate bot presence every 5 minutes
    async def rotate_presence():
        await bot.wait_until_ready()
        presences = [
            discord.Activity(type=discord.ActivityType.watching, name="Putting Rivvak to work"),
            discord.Activity(type=discord.ActivityType.watching, name=f"{total_keys:,} licenses"),
            discord.Activity(type=discord.ActivityType.playing, name="Protecting RivvakCommunity"),
            discord.Activity(type=discord.ActivityType.watching, name="Rivvak Community"),
        ]
        i = 0
        while not bot.is_closed():
            await bot.change_presence(activity=presences[i % len(presences)])
            i += 1
            await asyncio.sleep(300)  # 5 minutes

    bot.loop.create_task(rotate_presence())

    # ── One-time revocation list (processed once at startup then cleared) ──
    REVOKE_ON_STARTUP = [
        ("1299189351598526494", "Admin removal — all keys wiped by request"),
    ]
    for uid, reason in REVOKE_ON_STARTUP:
        try:
            result = await _api("post", "/admin/purge-all-keys", json={
                "discord_user_id": uid,
                "reason": reason,
                "admin_key_override": True,
            })
            deleted = result.get("deleted_count", 0)
            LOG.info(f"Startup hard-delete: {uid} → {deleted} key(s) erased. Reason: {reason}")
        except Exception as e:
            LOG.warning(f"Startup hard-delete failed for {uid}: {e}")


@bot.event
async def on_disconnect():
    LOG.warning("Discord WebSocket disconnected — Cloud Run may be cycling the container.")


@bot.event
async def on_resumed():
    LOG.info("Discord WebSocket resumed — bot reconnected without a cold start.")


@bot.event
async def on_member_join(member: discord.Member):
    """Diff invite use counts to identify which invite a new member used,
    then report that to the license server so referral counts (which
    previously always read 0, since Discord strips ?ref= URL params) can
    actually be attributed to the referrer."""
    if member.guild.id != GUILD_ID:
        return

    used_code = None
    try:
        invites = await member.guild.invites()
        new_uses = {inv.code: (inv.uses or 0) for inv in invites}

        for code, uses in new_uses.items():
            prev = _invite_uses.get(code)
            if prev is None:
                # Wasn't in cache before — a brand-new invite with 1 use
                # was almost certainly what this member used.
                if uses == 1:
                    used_code = code
                    break
            elif uses == prev + 1:
                used_code = code
                break

        _invite_uses.clear()
        _invite_uses.update(new_uses)
    except discord.Forbidden:
        LOG.warning(
            "Missing 'Manage Server' permission — cannot diff invite uses "
            f"for {member}'s join; referral attribution skipped.")
    except Exception as e:
        LOG.warning(f"Failed to diff invite uses on member join: {e}")

    if used_code is None:
        LOG.info(f"{member} joined — could not identify invite used")
        return

    LOG.info(f"{member} joined via invite code {used_code}")
    try:
        result = await _api("post", "/referral/track-join", json={
            "discord_invite_code": used_code,
            "referred_discord_id": str(member.id),
            "admin_key":           ADMIN_KEY,
        })
        if result.get("ok"):
            LOG.info(f"Referral tracked for {member} via invite {used_code}: {result}")
        else:
            LOG.info(f"Referral not tracked for {member} via invite {used_code}: {result}")
    except Exception as e:
        LOG.warning(f"/referral/track-join call failed for {member}: {e}")


@bot.event
async def on_invite_create(invite: discord.Invite):
    """Keep the invite-uses cache in sync as invites are created."""
    if invite.guild is None or invite.guild.id != GUILD_ID:
        return
    _invite_uses[invite.code] = invite.uses or 0


@bot.event
async def on_invite_delete(invite: discord.Invite):
    """Keep the invite-uses cache in sync as invites are deleted."""
    if invite.guild is None or invite.guild.id != GUILD_ID:
        return
    _invite_uses.pop(invite.code, None)


@bot.event
async def on_member_remove(member: discord.Member):
    if member.guild.id != GUILD_ID:
        return
    LOG.info(f"{member} left — pausing key")
    await _api("post", "/pause-by-discord", json={
        "discord_user_id": str(member.id),
        "reason": "Left Discord server",
    })

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if after.guild.id != GUILD_ID:
        return
    role = after.guild.get_role(ROLE_ID)
    if role is None:
        return

    # Lost Member role → pause key
    if role in before.roles and role not in after.roles:
        LOG.info(f"{after} lost Member role — pausing key")
        await _api("post", "/pause-by-discord", json={
            "discord_user_id": str(after.id),
            "reason": "Lost Member role",
        })

    # Gained Member role (e.g. Verified role granted) → send onboarding DM
    if role not in before.roles and role in after.roles:
        LOG.info(f"{after} gained Member role — sending onboarding DM")
        await _send_onboarding_dm(after)

# ── Onboarding DM helper ──────────────────────────────────────────────────────

async def _send_onboarding_dm(member: discord.Member):
    """Send a welcome / getting-started DM when a member receives the Member role."""
    embed = _embed_info(
        "🛡  Welcome to SteamGuard!",
        (
            "Here's how to get started:\n\n"
            "1. ✅  Complete verification in **#verify**\n"
            "2. ⬇️  Download the app: `/download`\n"
            "3. 🔑  Activate your license in the app\n"
            "4. 📊  Run `/stats` to see your first card\n"
            "5. 🔗  Use `/refer` to earn rewards\n\n"
            "Need help? Use `/support` anytime."
        ),
        footer="Your protection starts now",
    )
    try:
        await member.send(embed=embed)
    except discord.Forbidden:
        LOG.info(f"Could not DM onboarding to {member} (DMs disabled)")
    except Exception as e:
        LOG.warning(f"Onboarding DM error for {member}: {e}")

# ── Welcome embed in #get-key ─────────────────────────────────────────────────

async def _post_welcome_embed():
    if GETKEY_CHANNEL_ID == 0:
        return
    guild   = bot.get_guild(GUILD_ID)
    if not guild:
        return
    channel = guild.get_channel(GETKEY_CHANNEL_ID)
    if not channel:
        return

    embed = _embed(
        "🔑  SteamGuard — Get Your License Key",
        footer="Your key is locked to your machine. Do not share it.")
    embed.add_field(
        name="Requirements",
        value=(
            "✅  Be a **Member** of this server\n"
            "✅  Be **subscribed** to our YouTube channel\n"
            "✅  Accept the Terms of Service in the app"
        ),
        inline=False)
    embed.add_field(
        name="How to get your key",
        value=(
            "1️⃣  Make sure you have the **Member** role\n"
            "2️⃣  Type `!getkey` — your key will arrive in your **DMs**\n"
            "3️⃣  Download SteamGuard and enter the key\n"
            "4️⃣  Type `!linkyoutube` to link your YouTube account"
        ),
        inline=False)
    embed.add_field(
        name="Other commands",
        value="`!mykey` — check your key status\n`!linkyoutube` — link YouTube",
        inline=False)

    try:
        await channel.send(embed=embed)
    except Exception as e:
        LOG.warning(f"Could not post welcome embed: {e}")

# ── !getkey ───────────────────────────────────────────────────────────────────

@bot.command(name="getkey")
@in_getkey_channel()
async def get_key(ctx: commands.Context):
    """Issue a license key — only in #get-key, only with Member role."""
    if ctx.guild.id != GUILD_ID:
        return

    role = ctx.guild.get_role(ROLE_ID)
    if role not in ctx.author.roles:
        embed = _embed_error(
            "Missing Member Role",
            (
                "You need the **Member** role to get a key.\n\n"
                "Subscribe to our YouTube channel and make sure you're verified."
            ))
        await ctx.send(embed=embed, delete_after=20)
        return

    try:
        await ctx.message.delete()
    except Exception:
        pass

    # Enforce 1-key-per-user: if they already have an active key, send them to !mykey.
    existing = await _api_get(f"/admin/user-keys/{ctx.author.id}")
    if isinstance(existing, dict) and any(
        not k.get("revoked", False) for k in existing.get("keys", [])
    ):
        await ctx.send(
            f"{ctx.author.mention} You already have an active SteamGuard key. "
            "Use `!mykey` to view it, or contact support if you need to transfer "
            "it to a new machine.",
            delete_after=20,
        )
        return

    # Generate key via license server
    data = await _api("post", "/generate", json={
        "discord_user_id": str(ctx.author.id),
        "note":            str(ctx.author),
    })

    key = data.get("key")
    if not key:
        err = data.get("error", "Unknown error")
        await ctx.send(f"❌ Could not generate key: `{err}`", delete_after=15)
        return

    embed = _embed_success(
        "Your SteamGuard License Key",
        footer="Valid while you remain a Member + YouTube subscriber")
    embed.add_field(name="Key", value=f"```{key}```", inline=False)
    embed.add_field(
        name="⚠️  Keep this private",
        value="This key is **locked to your machine** and Discord account. Do NOT share it.",
        inline=False)
    embed.add_field(
        name="📋  How to activate",
        value=(
            "1. Launch **SteamGuard.exe**\n"
            "2. Accept the Terms of Service\n"
            "3. Enter your key + Discord User ID → **Activate**\n"
            "4. Come back here and run `!linkyoutube` to link your YouTube"
        ),
        inline=False)

    try:
        await ctx.author.send(embed=embed)
        confirm = _embed_success(
            "Key sent!",
            f"{ctx.author.mention} Your key is in your DMs.")
        await ctx.send(embed=confirm, delete_after=10)
    except discord.Forbidden:
        await ctx.send(
            f"❌ {ctx.author.mention} I can't DM you — enable **Allow direct messages from "
            "server members** in your Privacy Settings, then try again.",
            delete_after=20)

# ── !mykey ────────────────────────────────────────────────────────────────────

def _fmt_remaining(hours: float) -> str:
    """Format remaining hours as a human-readable string with status emoji."""
    if hours <= 0:
        return "⛔ **Expired**"
    if hours < 2:
        h, m = int(hours), int((hours % 1) * 60)
        return f"🔴 **{h}h {m}m** *(expiring soon!)*"
    if hours < 24:
        h, m = int(hours), int((hours % 1) * 60)
        return f"🟡 **{h}h {m}m**"
    days, rem_h = int(hours // 24), int(hours % 24)
    return f"🟢 **{days}d {rem_h}h**"


def _build_key_embed(k: dict, idx: int, total: int, username: str) -> discord.Embed:
    """Build a rich, beautifully formatted embed for a single key."""
    status = k.get("status", "active")
    color_map = {
        "active":  (0x5865F2, "🛡️", "ACTIVE"),    # Discord blurple / brand purple
        "paused":  (0xF59E0B, "⏸️", "PAUSED"),
        "revoked": (0xEF4444, "🚫", "REVOKED"),
    }
    color, shield, status_label = color_map.get(status, (0x64748B, "❓", "UNKNOWN"))

    # Key display — show actual key if available, else truncated hash
    key_plain = k.get("key")
    if key_plain:
        key_display = f"```\n{key_plain}\n```"
        key_note = ""
    else:
        key_display = f"```\n{k.get('key_hash_prefix', 'N/A')}\n```"
        key_note = "\n> *Full key not available — contact support*"

    # Metadata
    tier = k.get("tier", "free").title()
    tier_badge = "⭐" if tier.lower() == "premium" else "🆓"
    hwid_line = "🔗 Locked to your machine" if k.get("hwid_bound") else "🔓 Not yet activated — run the app to activate"
    remaining_line = _fmt_remaining(k.get("remaining_hours", 0))

    # Build rich description
    parts = [
        f"**{shield} Status:** `{status_label}`  •  {tier_badge} **{tier}**",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "🔑 **Your License Key**",
        key_display,
        key_note,
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"⏱️ **Time Remaining:** {remaining_line}",
        f"💻 **Machine:** {hwid_line}",
        f"✅ **Last Verified:** {k.get('last_verified', 'Never')}",
        f"🔄 **Verify Count:** {k.get('verify_count', 0)}×",
    ]
    if k.get("created_at"):
        parts.append(f"📅 **Created:** {k['created_at']}")
    if k.get("expires_at"):
        try:
            from datetime import datetime as _dt
            exp = _dt.fromisoformat(k["expires_at"].replace("Z", "+00:00"))
            parts.append(f"📆 **Expires:** <t:{int(exp.timestamp())}:R>")
        except Exception:
            pass
    if k.get("pause_reason"):
        parts += ["", f"> ⚠️ **Suspended:** {k['pause_reason']}"]
    if k.get("note"):
        parts += ["", f"> 📝 **Note:** {k['note']}"]

    parts += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "💡 **Earn more time:** `!daily` · `!rewards`",
        "📊 **View stats:** `!stats`",
        "🆘 **Need help:** `!support`",
    ]

    title = "🔐  Your SteamGuard Key"
    if total > 1:
        title += f"  ({idx}/{total})"

    embed = discord.Embed(
        title=title,
        description="\n".join(parts),
        color=color,
    )
    embed.set_author(
        name=f"AuthGuard — {username}",
        icon_url="https://i.imgur.com/5RHR6Xa.png",
    )
    embed.set_thumbnail(url="https://i.imgur.com/5RHR6Xa.png")
    embed.set_footer(
        text="AuthGuard • Rivvak Community  |  🔒 Delete this DM after reading  |  rivvak.app",
    )
    embed.timestamp = discord.utils.utcnow()
    return embed


@bot.command(name="mykey")
@in_getkey_channel()
async def my_key(ctx: commands.Context):
    """Show the user their own license key — actual key value, beautifully formatted."""
    try:
        await ctx.message.delete()
    except Exception:
        pass

    data = await _api_get(f"/admin/key-info/{ctx.author.id}")
    keys = data.get("keys", [])

    if not keys:
        embed = discord.Embed(
            title="🔑  No Key Found",
            description=(
                "You don't have a SteamGuard key yet.\n\n"
                "**How to get one:**\n"
                "→ Use `!getkey` in this channel\n"
                "→ Make sure you have the **Member** role\n"
                "→ Subscribe to [youtube.com/@Rivvak](https://youtube.com/@Rivvak)\n\n"
                "It's free — takes less than 60 seconds."
            ),
            color=0x64748B,
        )
        embed.set_author(name="AuthGuard • Rivvak Community",
                         icon_url="https://i.imgur.com/5RHR6Xa.png")
        embed.set_footer(text="AuthGuard • Rivvak Community  |  rivvak.app")
        embed.timestamp = discord.utils.utcnow()
        try:
            await ctx.author.send(embed=embed)
            await ctx.send(f"📬 {ctx.author.mention} Check your DMs.", delete_after=6)
        except discord.Forbidden:
            await ctx.send(embed=embed, delete_after=20)
        return

    total = len(keys)

    # Security warning — sent first
    warn_embed = discord.Embed(
        title="🔒  Security Notice",
        description=(
            "Your SteamGuard key is in the next message.\n\n"
            "**⚠️ Never share your key with anyone — not even admins.**\n"
            "If your key was compromised, use `!support` immediately.\n\n"
            "🗑️ **Delete this DM after reading for your own security.**"
        ),
        color=0xF59E0B,
    )
    warn_embed.set_footer(text="AuthGuard • Rivvak Community")
    warn_embed.timestamp = discord.utils.utcnow()

    embeds = [_build_key_embed(k, i + 1, total, str(ctx.author)) for i, k in enumerate(keys)]

    try:
        await ctx.author.send(embed=warn_embed)
        for embed in embeds:
            await ctx.author.send(embed=embed)
        await ctx.send(
            f"📬 {ctx.author.mention} Your key info has been sent to your DMs.",
            delete_after=6)
    except discord.Forbidden:
        await ctx.send(
            f"{ctx.author.mention} Please enable DMs from server members so I can send your key securely. "
            f"Go to **Server Settings → Privacy Settings → Allow direct messages from server members**.",
            delete_after=20)

# ── !linkyoutube ──────────────────────────────────────────────────────────────

@bot.command(name="linkyoutube")
@in_getkey_channel()
async def link_youtube(ctx: commands.Context):
    """Start YouTube OAuth flow so we can verify subscription."""
    try:
        await ctx.message.delete()
    except Exception:
        pass

    if not YT_CLIENT_ID:
        await ctx.send(
            "⚠️ YouTube linking is not configured yet. Ask an admin.",
            delete_after=10)
        return

    state    = f"{ctx.author.id}_{int(time.time())}"
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={YT_CLIENT_ID}"
        f"&redirect_uri={LICENSE_SERVER_URL}/youtube/callback"
        "&response_type=code"
        "&scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fyoutube.readonly"
        f"&state={state}"
        "&access_type=offline"
        "&prompt=consent"
    )

    embed = _embed_info(
        "🎥  Link Your YouTube Account",
        (
            "Click the link below to authorize SteamGuard to verify your YouTube subscription.\n\n"
            f"**[Click here to link YouTube]({auth_url})**\n\n"
            "This only checks if you're subscribed — we cannot see, edit, or delete anything."
        ),
        footer="Link expires in 10 minutes")

    try:
        await ctx.author.send(embed=embed)
        await ctx.send(f"📬 {ctx.author.mention} YouTube link sent to your DMs.", delete_after=8)
    except discord.Forbidden:
        await ctx.send(embed=embed, delete_after=60)

# ══════════════════════════════════════════════════════════════════════════════
# ██  !admin — Interactive Admin Panel  (Select-menu driven, clean layout)
# ══════════════════════════════════════════════════════════════════════════════

PAGE_SIZE = 8   # keys per page in the overview embed

# ── Helper: build the main overview embed ─────────────────────────────────────

def _admin_overview_embed(keys: list, page: int, admin: discord.Member) -> discord.Embed:
    """Clean paginated overview — one key per field, no inline cramming."""
    total  = len(keys)
    pages  = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    start  = page * PAGE_SIZE
    chunk  = keys[start: start + PAGE_SIZE]

    active  = sum(1 for k in keys if k.get("status") == "active")
    paused  = sum(1 for k in keys if k.get("status") == "paused")
    revoked = sum(1 for k in keys if k.get("status") == "revoked")

    embed = discord.Embed(
        title="🛡️  SteamGuard — Admin Panel",
        color=0x7C5CFC,
    )
    embed.set_author(
        name=f"Admin: {admin.display_name}",
        icon_url=admin.display_avatar.url,
    )

    # Summary bar
    embed.description = (
        f"**{total}** keys total   "
        f"🟢 {active} active   🟡 {paused} paused   🔴 {revoked} revoked\n"
        f"Page **{page + 1} / {pages}**   ·   showing {start + 1}–{min(start + PAGE_SIZE, total)}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    si = {"active": "🟢", "paused": "🟡", "revoked": "🔴"}

    for k in chunk:
        did    = k.get("discord_user_id") or "—"
        status = k.get("status", "active")
        icon   = si.get(status, "⚪")
        hwid   = "🔗 bound" if k.get("hwid_bound") else "🔓 unbound"
        tag    = f"  ·  ⚠️ {k['pause_reason']}" if k.get("pause_reason") else ""

        embed.add_field(
            name=f"{icon}  `{did}`",
            value=(
                f"`{k.get('key_hash', '')[:16]}…`   {hwid}\n"
                f"verified {k.get('last_verified', 'never')}  ·  {k.get('verify_count', 0)} checks{tag}"
            ),
            inline=False,
        )

    embed.set_footer(
        text="AuthGuard • Rivvak Community  |  rivvak.app  |  Use the dropdown to manage a user"
    )
    embed.timestamp = discord.utils.utcnow()
    return embed, pages


# ── Select menu: pick which user to manage ────────────────────────────────────

class _UserSelect(discord.ui.Select):
    def __init__(self, keys: list, page: int):
        chunk = keys[page * PAGE_SIZE: (page + 1) * PAGE_SIZE]
        si    = {"active": "🟢", "paused": "🟡", "revoked": "🔴"}
        opts  = []
        seen  = set()
        for k in chunk:
            did = k.get("discord_user_id", "unknown")
            if did in seen:
                continue
            seen.add(did)
            status = k.get("status", "active")
            opts.append(discord.SelectOption(
                label=did,
                value=did,
                emoji=si.get(status, "⚪"),
                description=f"{status.upper()}  ·  verified {k.get('last_verified', 'never')}",
            ))
        super().__init__(
            placeholder="👤  Select a user to manage…",
            options=opts or [discord.SelectOption(label="No users on this page", value="_none")],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id not in ADMIN_USER_IDS:
            await interaction.response.send_message("❌ Admins only.", ephemeral=True)
            return
        did = self.values[0]
        if did == "_none":
            await interaction.response.send_message("Nothing to select.", ephemeral=True)
            return
        # Find that user's key in the parent view
        parent: AdminPanelView = self.view
        key = next((k for k in parent.keys if k.get("discord_user_id") == did), None)
        if not key:
            await interaction.response.send_message("User not found in current data.", ephemeral=True)
            return
        parent.selected_did = did
        parent.selected_key = key
        parent._rebuild()
        await interaction.response.edit_message(embed=parent.build_embed(), view=parent)


# ── Main panel view ───────────────────────────────────────────────────────────

class AdminPanelView(discord.ui.View):
    def __init__(self, keys: list, admin: discord.Member, page: int = 0):
        super().__init__(timeout=180)
        self.keys         = keys
        self.admin        = admin
        self.page         = page
        self.total        = len(keys)
        self.pages        = max(1, (self.total + PAGE_SIZE - 1) // PAGE_SIZE)
        self.selected_did = None
        self.selected_key = None
        self._rebuild()

    def _rebuild(self):
        self.clear_items()

        # Row 0: user select dropdown
        self.add_item(_UserSelect(self.keys, self.page))

        # Row 1: action buttons — only shown when a user is selected
        if self.selected_did and self.selected_key:
            status = self.selected_key.get("status", "active")

            if status == "active":
                pause_btn = discord.ui.Button(
                    label="⏸  Pause User",
                    style=discord.ButtonStyle.secondary,
                    row=1,
                )
                pause_btn.callback = self._pause
                self.add_item(pause_btn)

            elif status == "paused":
                unpause_btn = discord.ui.Button(
                    label="▶  Unpause User",
                    style=discord.ButtonStyle.success,
                    row=1,
                )
                unpause_btn.callback = self._unpause
                self.add_item(unpause_btn)

            if status != "revoked":
                revoke_btn = discord.ui.Button(
                    label="🗑  Revoke All Keys",
                    style=discord.ButtonStyle.danger,
                    row=1,
                )
                revoke_btn.callback = self._revoke
                self.add_item(revoke_btn)

            clear_btn = discord.ui.Button(
                label="✖  Deselect",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
            clear_btn.callback = self._deselect
            self.add_item(clear_btn)

        # Row 4: pagination + refresh
        prev_btn = discord.ui.Button(
            label="◀  Prev",
            style=discord.ButtonStyle.secondary,
            disabled=(self.page == 0),
            row=4,
        )
        prev_btn.callback = self._prev
        self.add_item(prev_btn)

        next_btn = discord.ui.Button(
            label="Next  ▶",
            style=discord.ButtonStyle.secondary,
            disabled=(self.page >= self.pages - 1),
            row=4,
        )
        next_btn.callback = self._next
        self.add_item(next_btn)

        ref_btn = discord.ui.Button(
            label="🔄  Refresh",
            style=discord.ButtonStyle.primary,
            row=4,
        )
        ref_btn.callback = self._refresh
        self.add_item(ref_btn)

    def build_embed(self) -> discord.Embed:
        embed, _ = _admin_overview_embed(self.keys, self.page, self.admin)

        # If a user is selected, add a highlighted selected-user panel at top
        if self.selected_did and self.selected_key:
            k      = self.selected_key
            status = k.get("status", "active")
            si     = {"active": "🟢", "paused": "🟡", "revoked": "🔴"}
            icon   = si.get(status, "⚪")
            hwid   = "🔗 Bound to machine" if k.get("hwid_bound") else "🔓 Not yet activated"

            selected_text = (
                f"{icon} **{status.upper()}**   ·   {hwid}\n"
                f"Key: `{k.get('key_hash', '')[:20]}…`\n"
                f"Verified: {k.get('last_verified', 'never')}   ·   {k.get('verify_count', 0)} checks"
            )
            if k.get("pause_reason"):
                selected_text += f"\nReason: {k['pause_reason']}"

            embed.insert_field_at(
                0,
                name=f"▶  Selected: `{self.selected_did}`",
                value=selected_text,
                inline=False,
            )
            embed.color = 0xF59E0B   # amber highlight when user selected

        return embed

    # ── auth guard ──────────────────────────────────────────────────────────
    async def _check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in ADMIN_USER_IDS:
            await interaction.response.send_message("❌ Admins only.", ephemeral=True)
            return False
        return True

    # ── actions ──────────────────────────────────────────────────────────────
    async def _pause(self, interaction: discord.Interaction):
        if not await self._check(interaction): return
        did = self.selected_did
        await interaction.response.defer(ephemeral=True)
        res   = await _api("post", "/admin/pause-by-discord", json={
            "discord_user_id": did,
            "reason": f"Paused by {interaction.user} via admin panel",
        })
        count = res.get("paused_count", res.get("count", "?"))
        for k in self.keys:
            if k.get("discord_user_id") == did:
                k["status"] = "paused"
                k["pause_reason"] = f"Paused by {interaction.user}"
        if self.selected_key:
            self.selected_key["status"] = "paused"
            self.selected_key["pause_reason"] = f"Paused by {interaction.user}"
        self._rebuild()
        await interaction.followup.send(f"⏸️ Paused **{count}** key(s) for `{did}`", ephemeral=True)
        await interaction.edit_original_response(embed=self.build_embed(), view=self)

    async def _unpause(self, interaction: discord.Interaction):
        if not await self._check(interaction): return
        did = self.selected_did
        await interaction.response.defer(ephemeral=True)
        res   = await _api("post", "/admin/unpause-by-discord", json={"discord_user_id": did})
        count = res.get("unpaused_count", res.get("count", "?"))
        for k in self.keys:
            if k.get("discord_user_id") == did:
                k["status"] = "active"
                k["pause_reason"] = None
        if self.selected_key:
            self.selected_key["status"] = "active"
            self.selected_key["pause_reason"] = None
        self._rebuild()
        await interaction.followup.send(f"▶️ Unpaused **{count}** key(s) for `{did}`", ephemeral=True)
        await interaction.edit_original_response(embed=self.build_embed(), view=self)

    async def _revoke(self, interaction: discord.Interaction):
        if not await self._check(interaction): return
        did     = self.selected_did
        confirm = _ConfirmRevokeView(did, self)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="⚠️  Confirm Revocation",
                description=(
                    f"You are about to **permanently revoke all keys** for:\n"
                    f"```\n{did}\n```\n"
                    "**This cannot be undone.** The user loses access immediately."
                ),
                color=0xEF4444,
            ),
            view=confirm,
            ephemeral=True,
        )

    async def _deselect(self, interaction: discord.Interaction):
        if not await self._check(interaction): return
        self.selected_did = None
        self.selected_key = None
        self._rebuild()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _prev(self, interaction: discord.Interaction):
        if not await self._check(interaction): return
        self.page = max(0, self.page - 1)
        self.selected_did = None
        self.selected_key = None
        self._rebuild()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _next(self, interaction: discord.Interaction):
        if not await self._check(interaction): return
        self.page = min(self.pages - 1, self.page + 1)
        self.selected_did = None
        self.selected_key = None
        self._rebuild()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _refresh(self, interaction: discord.Interaction):
        if not await self._check(interaction): return
        await interaction.response.defer()
        fresh      = await _api_get("/admin/list-keys", filter="all")
        self.keys  = fresh.get("keys", [])
        self.total = len(self.keys)
        self.pages = max(1, (self.total + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page  = min(self.page, self.pages - 1)
        self.selected_did = None
        self.selected_key = None
        self._rebuild()
        await interaction.edit_original_response(embed=self.build_embed(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class _ConfirmRevokeView(discord.ui.View):
    def __init__(self, did: str, parent: AdminPanelView):
        super().__init__(timeout=30)
        self.did    = did
        self.parent = parent

    @discord.ui.button(label="✅  Yes, Revoke", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in ADMIN_USER_IDS:
            await interaction.response.send_message("❌ Admins only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        res   = await _api("post", "/revoke-by-discord", json={
            "discord_user_id": self.did,
            "reason": f"Revoked by {interaction.user} via admin panel",
        })
        count = res.get("revoked_count", res.get("count", "?"))
        self.parent.keys      = [k for k in self.parent.keys if k.get("discord_user_id") != self.did]
        self.parent.total     = len(self.parent.keys)
        self.parent.pages     = max(1, (self.parent.total + PAGE_SIZE - 1) // PAGE_SIZE)
        self.parent.page      = min(self.parent.page, self.parent.pages - 1)
        self.parent.selected_did = None
        self.parent.selected_key = None
        self.parent._rebuild()
        await interaction.followup.send(
            f"🗑️ Permanently revoked **{count}** key(s) for `{self.did}`", ephemeral=True)
        try:
            await interaction.message.edit(embed=self.parent.build_embed(), view=self.parent)
        except Exception:
            pass

    @discord.ui.button(label="❌  Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Cancelled.", ephemeral=True)


@bot.command(name="admin")
@is_admin()
async def admin_panel(ctx: commands.Context, filter: str = "all"):
    """Admin: open the interactive key management panel with dropdown user selection."""
    try:
        await ctx.message.delete()
    except Exception:
        pass
    loading = await ctx.send("⏳ Loading admin panel…")
    data  = await _api_get("/admin/list-keys", filter=filter)
    keys  = data.get("keys", [])
    if not keys:
        await loading.edit(content=f"No keys found (filter: `{filter}`).")
        return
    view  = AdminPanelView(keys=keys, admin=ctx.author, page=0)
    await loading.edit(content=None, embed=view.build_embed(), view=view)


# ── !removeallkeys @user ──────────────────────────────────────────────────────

@bot.command(name="removeallkeys")
@is_admin()
async def remove_all_keys(ctx: commands.Context, member: discord.Member, *, reason: str = "Removed by admin"):
    """Admin: permanently revoke ALL keys for a user. Usage: !removeallkeys @User [reason]"""
    embed = discord.Embed(
        title="⚠️  Confirm Full Erasure",
        description=(
            f"Completely erase **all keys and data** for {member.mention}?\n"
            f"**ID:** `{member.id}`\n"
            f"**Reason:** {reason}\n\n"
            f"Keys, session, rewards, and YT link will all be **deleted** — no trace.\n"
            f"They will be able to get a new key afterwards. Use `!ban` to also block future keys."
        ),
        color=0xEF4444,
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    class QuickDeleteView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=30)

        @discord.ui.button(label="🗑️  Yes, Erase All Traces", style=discord.ButtonStyle.danger)
        async def confirm(self_, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id not in ADMIN_USER_IDS:
                await interaction.response.send_message("❌ Admins only.", ephemeral=True)
                return
            await interaction.response.defer()
            # Hard delete — no revoked record left behind
            res   = await _api("post", "/admin/delete-by-discord", json={
                "discord_user_id": str(member.id),
                "reason": reason,
            })
            count = res.get("deleted_count", 0)
            done  = discord.Embed(
                title="🗑️  All Data Erased",
                description=(
                    f"Permanently deleted **{count}** key(s) for {member.mention}\n"
                    f"**ID:** `{member.id}`\n"
                    f"**Reason:** {reason}\n\n"
                    f"No trace remains. They can generate a new key normally."
                ),
                color=0x22D3A5,
            )
            done.set_thumbnail(url=member.display_avatar.url)
            done.set_footer(text=f"Action by {interaction.user}  |  AuthGuard • rivvak.app")
            done.timestamp = discord.utils.utcnow()
            for item in self_.children:
                item.disabled = True
            await interaction.edit_original_response(embed=done, view=self_)
            LOG.info(f"Admin {ctx.author} hard-deleted all keys for {member} ({member.id}) — {reason}")

        @discord.ui.button(label="❌  Cancel", style=discord.ButtonStyle.secondary)
        async def cancel(self_, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_message("Cancelled.", ephemeral=True)
            await ctx.message.delete()

    await ctx.send(embed=embed, view=QuickDeleteView())

# ── Admin: !listkeys ──────────────────────────────────────────────────────────



# ── Admin: !ban @user [reason] ────────────────────────────────────────────────

@bot.command(name="ban")
@is_admin()
async def cmd_ban(ctx: commands.Context, member: discord.Member, *, reason: str = "Banned by admin"):
    """Admin: revoke all keys AND permanently block a user from generating new keys."""
    embed = discord.Embed(
        title="⛔  Confirm Ban",
        description=(
            f"Ban {member.mention} from SteamGuard?\n"
            f"**ID:** `{member.id}`\n"
            f"**Reason:** {reason}\n\n"
            f"• All active keys will be **revoked** (record kept)\n"
            f"• User is **permanently blocked** from generating new keys\n"
            f"Use `!unban @user` to lift the ban later."
        ),
        color=0xEF4444,
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    class BanView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=30)

        @discord.ui.button(label="⛔  Yes, Ban User", style=discord.ButtonStyle.danger)
        async def confirm(self_, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id not in ADMIN_USER_IDS:
                await interaction.response.send_message("❌ Admins only.", ephemeral=True)
                return
            await interaction.response.defer()
            res = await _api("post", "/admin/ban-user", json={
                "discord_user_id": str(member.id),
                "reason": reason,
            })
            revoked = res.get("revoked_count", 0)
            done = discord.Embed(
                title="⛔  User Banned",
                description=(
                    f"{member.mention} has been banned from SteamGuard.\n"
                    f"**ID:** `{member.id}`\n"
                    f"**Keys revoked:** {revoked}\n"
                    f"**Reason:** {reason}\n\n"
                    f"They cannot generate a new key. Use `!unban @{member.display_name}` to lift."
                ),
                color=0xEF4444,
            )
            done.set_thumbnail(url=member.display_avatar.url)
            done.set_footer(text=f"Action by {interaction.user}  |  AuthGuard • rivvak.app")
            done.timestamp = discord.utils.utcnow()
            for item in self_.children:
                item.disabled = True
            await interaction.edit_original_response(embed=done, view=self_)
            LOG.info(f"Admin {ctx.author} banned {member} ({member.id}) — {reason}")

        @discord.ui.button(label="❌  Cancel", style=discord.ButtonStyle.secondary)
        async def cancel(self_, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_message("Cancelled.", ephemeral=True)
            await ctx.message.delete()

    await ctx.send(embed=embed, view=BanView())


# ── Admin: !unban @user ───────────────────────────────────────────────────────

@bot.command(name="unban")
@is_admin()
async def cmd_unban(ctx: commands.Context, member: discord.Member):
    """Admin: lift a ban so a user can generate keys again."""
    await _api("post", "/admin/unban-user", json={"discord_user_id": str(member.id)})
    embed = discord.Embed(
        title="✅  Ban Lifted",
        description=(
            f"{member.mention} can now generate SteamGuard keys again.\n"
            f"**ID:** `{member.id}`"
        ),
        color=0x22D3A5,
    )
    embed.set_footer(text=f"Action by {ctx.author}  |  AuthGuard • rivvak.app")
    embed.timestamp = discord.utils.utcnow()
    await ctx.send(embed=embed)
    LOG.info(f"Admin {ctx.author} unbanned {member} ({member.id})")


@bot.command(name="listkeys")
@is_admin()
async def list_keys(ctx: commands.Context, filter: str = "all"):
    """Admin: list all keys with stats. filter: all|active|paused|revoked"""
    data = await _api_get("/admin/list-keys", filter=filter)
    keys = data.get("keys", [])
    count = data.get("count", 0)

    if not keys:
        await ctx.send(f"No keys found (filter: `{filter}`).")
        return

    # Build a text table — send in chunks if > 2000 chars
    lines = [f"**SteamGuard Keys** — `{filter}` ({count} total)\n```"]
    lines.append(f"{'Discord ID':<20} {'Status':<8} {'Running':<14} {'Verified':<12} {'Checks':>6}")
    lines.append("─" * 66)

    for k in keys:
        icon    = {"active": "✓", "paused": "⏸", "revoked": "✗"}.get(k["status"], "?")
        did     = (k.get("discord_user_id") or "unknown")[:18]
        status  = f"{icon} {k['status'][:6]}"
        running = k.get("running_since", "—")[:13]
        verified= k.get("last_verified", "—")[:11]
        checks  = str(k.get("verify_count", 0))
        lines.append(f"{did:<20} {status:<8} {running:<14} {verified:<12} {checks:>6}")

    lines.append("```")
    msg = "\n".join(lines)

    # Discord message limit = 2000 chars; chunk if needed
    chunks, current = [], ""
    for line in msg.split("\n"):
        if len(current) + len(line) + 1 > 1900:
            chunks.append(current)
            current = line + "\n"
        else:
            current += line + "\n"
    if current:
        chunks.append(current)

    for chunk in chunks:
        await ctx.send(chunk)

# ── Admin: !keyinfo @user ─────────────────────────────────────────────────────

@bot.command(name="keyinfo")
@is_admin()
async def key_info(ctx: commands.Context, member: discord.Member):
    """Admin: show all key details for a specific user."""
    data = await _api_get(f"/admin/key-info/{member.id}")
    keys = data.get("keys", [])

    embed = _embed_info(f"Keys for {member.display_name}")

    if not keys:
        embed.description = "No keys found."
    else:
        for k in keys:
            status_icon = {"active": "🟢", "paused": "🟡", "revoked": "🔴"}.get(k["status"], "⚪")
            embed.add_field(
                name=f"{status_icon} `{k['key_hash']}`",
                value=(
                    f"Status: **{k['status']}**\n"
                    f"HWID bound: {k['hwid_bound']}\n"
                    f"Running: {k.get('running_since', '—')}\n"
                    f"Last verified: {k.get('last_verified', '—')}\n"
                    f"Checks: {k.get('verify_count', 0)}\n"
                    + (f"Pause reason: {k['pause_reason']}\n" if k.get("pause_reason") else "")
                ),
                inline=False)

    await ctx.send(embed=embed)

# ── Admin: !pausekey @user [reason] ──────────────────────────────────────────

@bot.command(name="pausekey")
@is_admin()
async def pause_key(ctx: commands.Context, member: discord.Member, *, reason: str = "Suspended by admin"):
    """Admin: immediately suspend a user's key."""
    data = await _api("post", "/pause-by-discord", json={
        "discord_user_id": str(member.id),
        "reason": reason,
    })
    count = data.get("paused_count", 0)
    embed = _embed_warn(
        "Key Paused",
        f"Paused **{count}** key(s) for {member.mention}\nReason: `{reason}`")
    await ctx.send(embed=embed)
    LOG.info(f"Admin {ctx.author} paused key for {member} — {reason}")

# ── Admin: !unpausekey @user ──────────────────────────────────────────────────

@bot.command(name="unpausekey")
@is_admin()
async def unpause_key(ctx: commands.Context, member: discord.Member):
    """Admin: resume a suspended user's key. Requires their raw key — ask them !mykey first."""
    await ctx.send(
        f"To unpause, use the raw key. Ask {member.mention} to run `!mykey` to get their key hash,\n"
        f"then use the API directly:\n"
        f"```\nPOST {LICENSE_SERVER_URL}/unpause\n"
        f'Body: {{"key": "SGRD-XXXX-XXXX-XXXX-XXXX"}}\n'
        f"Header: x-admin-key: YOUR_ADMIN_KEY\n```\n"
        f"(Full !unpausekey with stored keys coming in next update)")

# ── Admin: !revokekey @user [reason] ─────────────────────────────────────────

@bot.command(name="revokekey")
@is_admin()
async def revoke_key(ctx: commands.Context, member: discord.Member, *, reason: str = "Revoked by admin"):
    """Admin: permanently revoke a user's key."""
    data = await _api("post", "/revoke-by-discord", json={
        "discord_user_id": str(member.id),
        "reason": reason,
    })
    count = data.get("revoked_count", 0)
    embed = _embed_error(
        "Key Revoked",
        f"Permanently revoked **{count}** key(s) for {member.mention}\nReason: `{reason}`")
    await ctx.send(embed=embed)
    LOG.info(f"Admin {ctx.author} revoked key for {member} — {reason}")

# ── Admin: !sgstatus ──────────────────────────────────────────────────────────

@bot.command(name="sgstatus")
@is_admin()
async def sg_status(ctx: commands.Context):
    """Admin: bot + server health with aggregate stats."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{LICENSE_SERVER_URL}/health")
        server_ok = r.status_code == 200
        server_ts = r.json().get("ts", "?") if server_ok else "—"
    except Exception:
        server_ok = False
        server_ts = "—"

    # Fetch aggregate stats
    stats_data = {}
    try:
        stats_data = await _api_get("/stats/server")
    except Exception:
        pass

    total_keys  = stats_data.get("total_keys", "N/A")
    active_keys = stats_data.get("active_keys", "N/A")
    total_heals = stats_data.get("total_heals", "N/A")

    embed = _embed_success("SteamGuard System Status") if server_ok else _embed_error("SteamGuard System Status")
    embed.add_field(name="Bot",         value="🟢 Online",                                   inline=True)
    embed.add_field(name="Server",      value=f"{'🟢 Online' if server_ok else '🔴 Down'}",   inline=True)
    embed.add_field(name="Server time", value=server_ts,                                      inline=True)
    embed.add_field(name="Guild",       value=str(GUILD_ID),                                  inline=True)
    embed.add_field(name="Channel",     value="All channels", inline=True)
    embed.add_field(name="​",           value="​",                                             inline=True)  # spacer
    embed.add_field(name="Total Keys",  value=f"{total_keys:,}" if isinstance(total_keys, int) else str(total_keys),  inline=True)
    embed.add_field(name="Active Keys", value=f"{active_keys:,}" if isinstance(active_keys, int) else str(active_keys), inline=True)
    embed.add_field(name="Total Heals", value=f"{total_heals:,}" if isinstance(total_heals, int) else str(total_heals), inline=True)
    await ctx.send(embed=embed)

# ── Error handler ─────────────────────────────────────────────────────────────

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return  # silently ignore unknown commands
    if isinstance(error, commands.CheckFailure):
        return  # already handled by channel routing
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=_embed_error(
            "Missing argument",
            f"Usage: `!{ctx.command.name} {ctx.command.signature}`\n"
            f"Run `!help` for all commands.",
        ))
        return
    if isinstance(error, commands.MemberNotFound):
        await ctx.send(embed=_embed_error("Member not found", "Mention them with @username."))
        return
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(embed=_embed_warn(
            "Slow down",
            f"Try again in **{error.retry_after:.0f}s**. Your key and balance are safe."
        ))
        return
    # Unknown error — log it, reassure user
    LOG.error(f"Unhandled error in {ctx.command}: {error}", exc_info=True)
    await ctx.send(embed=_embed_error(
        "Something went wrong",
        "An unexpected error occurred. **Your key and balance are safe** — nothing was changed.\n"
        "If this keeps happening, run `!support`.",
    ))



# ── !help  (overrides default; also aliased as !commandhelp, !commands, !cmds) ─

# Remove the default help command so we can register our own named "help"
bot.remove_command("help")


@bot.command(name="help", aliases=["commandhelp", "commands", "cmds"])
async def cmd_help(ctx: commands.Context, *, section: str = ""):
    """Shows this help menu. Type !help <section> for details on a section."""
    is_adm = (ctx.author.id in ADMIN_USER_IDS
              or (ctx.guild and ctx.author.guild_permissions.administrator))

    getkey_ch  = ctx.guild.get_channel(GETKEY_CHANNEL_ID) if ctx.guild and GETKEY_CHANNEL_ID else None
    ot_ch      = ctx.guild.get_channel(OFF_TOPIC_CHANNEL_ID) if ctx.guild and OFF_TOPIC_CHANNEL_ID else None
    getkey_ref = getkey_ch.mention if getkey_ch else "#get-key"
    ot_ref     = ot_ch.mention if ot_ch else "#off-topic"

    section = section.lower().strip()

    # ── Detailed section views ──────────────────────────────────────────────────
    if section in ("key", "keys", "license"):
        e = discord.Embed(
            title="🔑  Getting Your Key",
            description=(
                f"All key commands are used in {getkey_ref}.\n"
                "Your key is tied to **one device** and **one Discord account**."
            ),
            color=0x7C5CFC,
        )
        e.add_field(name="`!getkey`", value="Generates your personal SteamGuard license key.\n"
                    "Requirements: Member role + subscribed to YouTube.", inline=False)
        e.add_field(name="`!mykey`", value="Shows your current key, status (active/paused/revoked), "
                    "and how much time is left.", inline=False)
        e.add_field(name="`!linkyoutube`", value="Links your YouTube account so the bot can verify "
                    "your subscription automatically.", inline=False)
        e.set_footer(text="One key per account • rivvak.app")
        await ctx.send(embed=e)
        return

    if section in ("account", "profile", "status"):
        e = discord.Embed(
            title="👤  Account & Profile",
            description=f"Use these commands in {ot_ref}.",
            color=0x7C5CFC,
        )
        e.add_field(name="`!status`", value="Full snapshot: license tier, XP, badges, and account health.", inline=False)
        e.add_field(name="`!stats`", value="Public protection card showing how many heals SteamGuard has done for you.", inline=False)
        e.add_field(name="`!refer`", value="Get your unique referral link. Every friend who activates gives you +3 hours free.", inline=False)
        e.add_field(name="`!resetdevice`", value="Resets your HWID binding so you can move SteamGuard to a new PC.\n"
                    "⏳ 30-day cooldown between resets.", inline=False)
        e.set_footer(text="rivvak.app")
        await ctx.send(embed=e)
        return

    if section in ("reward", "rewards", "daily", "earn"):
        e = discord.Embed(
            title="🎁  Earning Free Time",
            description="Multiple ways to extend your SteamGuard subscription for free.",
            color=0x7C5CFC,
        )
        e.add_field(name="`!rewards`", value="See every reward you can earn — daily check-ins, referrals, badges, and more.", inline=False)
        e.add_field(name="`!daily`", value="Claim a free **+30 minutes** every ~20 hours. Just type it in any channel.", inline=False)
        e.add_field(name="`!invitefriends`", value="Get your referral message ready to share. Each friend who activates = **+3 hours**.", inline=False)
        e.add_field(name="`!checkbadges`", value="Checks whether you've unlocked any new badges and announces them.", inline=False)
        e.set_footer(text="Rewards stack • rivvak.app")
        await ctx.send(embed=e)
        return

    if section in ("community", "social", "server"):
        e = discord.Embed(
            title="🌐  Community",
            description="Engage with the Rivvak community.",
            color=0x7C5CFC,
        )
        e.add_field(name="`!leaderboard`", value="Weekly top-10 players ranked by most heals.", inline=False)
        e.add_field(name="`!vote`", value="Vote on the next SteamGuard feature. Your opinion shapes the roadmap.", inline=False)
        e.add_field(name="`!download`", value="Get the latest SteamGuard installer link.", inline=False)
        e.add_field(name="`!support`", value="Opens a support ticket form. A staff member will respond shortly.", inline=False)
        e.set_footer(text="discord.gg/RTHM8YhpE")
        await ctx.send(embed=e)
        return

    if section in ("admin", "staff") and is_adm:
        e = discord.Embed(
            title="⚙️  Admin Commands",
            description="Staff-only commands. All require admin role or admin Discord ID.",
            color=0xF59E0B,
        )
        e.add_field(name="`!admin`", value="Opens the interactive key management panel with search, pause, and revoke controls.", inline=False)
        e.add_field(name="`!listkeys [all|active|paused|revoked]`", value="Table of every license key with status and stats.", inline=False)
        e.add_field(name="`!keyinfo @user`", value="Deep-dive on all keys linked to a specific user.", inline=False)
        e.add_field(name="`!pausekey @user [reason]`", value="Immediately suspends a user's key. They'll see 'suspended' in the app.", inline=False)
        e.add_field(name="`!unpausekey @user`", value="Re-activates a paused key.", inline=False)
        e.add_field(name="`!revokekey @user [reason]`", value="Permanently revokes a key. Record is kept in Firestore.", inline=False)
        e.add_field(name="`!ban @user [reason]`", value="Revokes all keys **and** blacklists the user from ever getting a new one.", inline=False)
        e.add_field(name="`!unban @user`", value="Lifts a ban so the user can generate a key again.", inline=False)
        e.add_field(name="`!removeallkeys @user [reason]`", value="Hard-deletes every key and all data for a user. No trace left.", inline=False)
        e.add_field(name="`!grantreward @user <trigger>`", value="Manually grants a reward (e.g. `youtube_sub`, `referral`).", inline=False)
        e.add_field(name="`!sgstatus`", value="Live bot + server health dashboard with aggregate stats.", inline=False)
        e.set_footer(text="Admin panel • rivvak.app")
        await ctx.send(embed=e)
        return

    # ── Main help menu ──────────────────────────────────────────────────────────
    embed = discord.Embed(
        title="🛡️  AuthGuard — Command Help",
        description=(
            f"**Welcome to SteamGuard by Rivvak.**\n"
            f"Type `!help <section>` for detailed info on any category.\n\n"
            f"Key commands → {getkey_ref}   •   Everything else → {ot_ref}"
        ),
        color=0x7C5CFC,
    )

    embed.add_field(
        name="🔑  License Key Commands",
        value=(
            "`!getkey` — Get your SteamGuard license key\n"
            "`!mykey` — Check your key & time remaining\n"
            "`!linkyoutube` — Link YouTube for auto-verification\n"
            "➜ `!help key` for details"
        ),
        inline=False,
    )

    embed.add_field(
        name="👤  Account & Profile",
        value=(
            "`!status` — Full account overview (tier, XP, badges)\n"
            "`!stats` — Your public protection stats\n"
            "`!refer` — Get your referral link\n"
            "`!resetdevice` — Move to a new PC (30d cooldown)\n"
            "➜ `!help account` for details"
        ),
        inline=False,
    )

    embed.add_field(
        name="🎁  Earn Free Time",
        value=(
            "`!daily` — +30 min free every ~20 hours\n"
            "`!rewards` — All reward options\n"
            "`!invitefriends` — +3h per friend you refer\n"
            "`!checkbadges` — Check for badge unlocks\n"
            "➜ `!help rewards` for details"
        ),
        inline=False,
    )

    embed.add_field(
        name="🌐  Community",
        value=(
            "`!leaderboard` — Weekly heals top-10\n"
            "`!vote` — Shape the next feature\n"
            "`!download` — Latest installer\n"
            "`!support` — Open a support ticket\n"
            "➜ `!help community` for details"
        ),
        inline=False,
    )

    if is_adm:
        embed.add_field(
            name="⚙️  Admin Panel",
            value=(
                "`!admin` — Interactive key management panel\n"
                "`!ban @user` — Revoke + blacklist a user\n"
                "`!removeallkeys @user` — Hard delete all data\n"
                "➜ `!help admin` for full list"
            ),
            inline=False,
        )

    embed.set_footer(text="SteamGuard • rivvak.app • discord.gg/RTHM8YhpE")
    embed.timestamp = discord.utils.utcnow()
    await ctx.send(embed=embed)


# ── Daily membership sweep ────────────────────────────────────────────────────

@tasks.loop(hours=24)
async def daily_membership_sweep():
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        LOG.warning("Sweep: guild not found")
        return

    role = guild.get_role(ROLE_ID)
    if not role:
        LOG.warning("Sweep: role not found")
        return

    members_with_role = {str(m.id) for m in role.members}
    LOG.info(f"Sweep: {len(members_with_role)} members have role")

    data = await _api_get("/admin/active-discord-ids")
    active_ids = data.get("ids", [])

    paused = 0
    for discord_id in active_ids:
        if discord_id not in members_with_role:
            await _api("post", "/pause-by-discord", json={
                "discord_user_id": discord_id,
                "reason": "Daily sweep: no longer has Member role",
            })
            paused += 1

    LOG.info(f"Sweep complete: {paused} keys paused")

@daily_membership_sweep.before_loop
async def before_sweep():
    await bot.wait_until_ready()


# ═══════════════════════════════════════════════════════════════════════════════
# ── Scheduled Monitoring Tasks ─────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

# ── Morning Deployment Health Check (daily, 9 AM UTC) ────────────────────────

@tasks.loop(hours=24)
async def morning_health_check():
    """Every morning: ping the license server and report deployment health + error snapshot."""
    await bot.wait_until_ready()
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    # Send to moderator channel
    channel = guild.get_channel(MOD_CHANNEL_ID)
    if not channel:
        LOG.warning("Morning health check: MOD_CHANNEL_ID not set or channel not found")
        return

    try:
        import time as _time
        t0 = _time.monotonic()
        data = await _api_get("/stats/server")
        latency_ms = round((_time.monotonic() - t0) * 1000)
        total_keys   = data.get("total_keys", "?")
        active_keys  = data.get("active_keys", "?")
        paused_keys  = data.get("paused_keys", "?")
        revoked_keys = data.get("revoked_keys", "?")
        status_icon  = "🟢" if latency_ms < 800 else "🟡" if latency_ms < 2000 else "🔴"

        embed = discord.Embed(
            title=f"{status_icon}  SteamGuard Deployment Health",
            description=(
                f"**Daily morning check** — license server is responding.\n"
                f"Latency: **{latency_ms} ms**"
            ),
            color=0x22D3A5 if latency_ms < 800 else 0xF59E0B if latency_ms < 2000 else 0xEF4444,
        )
        embed.add_field(name="Total Keys", value=str(total_keys), inline=True)
        embed.add_field(name="Active",     value=str(active_keys), inline=True)
        embed.add_field(name="Paused",     value=str(paused_keys), inline=True)
        embed.add_field(name="Revoked",    value=str(revoked_keys), inline=True)
        embed.set_footer(text="AuthGuard Monitor • rivvak.app")
        embed.timestamp = discord.utils.utcnow()
        await channel.send(embed=embed)
        LOG.info(f"Morning health check: OK ({latency_ms}ms)")
    except Exception as e:
        embed = discord.Embed(
            title="🔴  License Server Unreachable",
            description=f"Morning health check **FAILED**.\n```{e}```",
            color=0xEF4444,
        )
        embed.set_footer(text="AuthGuard Monitor • rivvak.app")
        embed.timestamp = discord.utils.utcnow()
        await channel.send(embed=embed)
        LOG.error(f"Morning health check failed: {e}")

@morning_health_check.before_loop
async def before_morning_check():
    await bot.wait_until_ready()

# ── License Server Uptime Check (every 10 minutes) ───────────────────────────

@tasks.loop(minutes=10)
async def uptime_check():
    """Every 10 minutes: ping /health. If it fails, alert in the off-topic channel."""
    await bot.wait_until_ready()
    try:
        data = await _api_get("/health")
        if data.get("status") not in ("ok", "healthy", None):
            raise ValueError(f"Unexpected health status: {data}")
        # Silently pass — only alert on failure
    except Exception as e:
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            return
        channel = guild.get_channel(MOD_CHANNEL_ID)
        if not channel:
            return
        embed = discord.Embed(
            title="🔴  OUTAGE DETECTED — License Server Down",
            description=(
                f"The SteamGuard license server failed an uptime check.\n"
                f"```{e}```\n"
                f"URL: `{LICENSE_SERVER_URL}`\n"
                f"Action: check Cloud Run logs → `fabled-mystery-474200-i1`"
            ),
            color=0xEF4444,
        )
        embed.set_footer(text="AuthGuard Uptime Monitor • rivvak.app")
        embed.timestamp = discord.utils.utcnow()
        await channel.send(embed=embed)
        LOG.error(f"Uptime check FAILED: {e}")

@uptime_check.before_loop
async def before_uptime_check():
    await bot.wait_until_ready()

# ── Weekly Key Generation Spike Audit (every 7 days) ─────────────────────────

@tasks.loop(hours=168)  # 7 days
async def weekly_key_audit():
    """Weekly: check for any user with suspiciously high verify counts or duplicate key attempts."""
    await bot.wait_until_ready()
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    channel = guild.get_channel(MOD_CHANNEL_ID)
    if not channel:
        return
    try:
        data   = await _api_get("/admin/list-keys", filter="all")
        keys   = data.get("keys", [])
        count  = data.get("count", 0)

        # Find users with unusually high verify counts (>200/week is suspicious)
        THRESHOLD = 200
        spikes = [
            k for k in keys
            if (k.get("verify_count") or 0) > THRESHOLD
        ]

        embed = discord.Embed(
            title="📋  Weekly Key Audit Report",
            description=(
                f"**Total keys in system:** {count}\n"
                f"**High-activity accounts (>{THRESHOLD} verifies):** {len(spikes)}"
            ),
            color=0xF59E0B if spikes else 0x22D3A5,
        )

        if spikes:
            spike_lines = []
            for k in spikes[:10]:  # Cap at 10 to avoid embed overflow
                did   = k.get("discord_user_id", "unknown")
                vc    = k.get("verify_count", 0)
                stat  = k.get("status", "?")
                spike_lines.append(f"`{did}` — {vc} verifies — {stat}")
            embed.add_field(
                name="⚠️  Suspicious Accounts",
                value="\n".join(spike_lines) or "None",
                inline=False,
            )
            embed.add_field(
                name="Recommended Action",
                value="Review with `!keyinfo @user` — use `!ban @user` if abuse confirmed.",
                inline=False,
            )
        else:
            embed.add_field(name="✅  No Anomalies", value="All accounts within normal activity range.", inline=False)

        embed.set_footer(text="AuthGuard Weekly Audit • rivvak.app")
        embed.timestamp = discord.utils.utcnow()
        await channel.send(embed=embed)
        LOG.info(f"Weekly audit complete: {count} keys, {len(spikes)} spikes")
    except Exception as e:
        LOG.error(f"Weekly key audit failed: {e}")

@weekly_key_audit.before_loop
async def before_weekly_audit():
    await bot.wait_until_ready()



# ── Weekly Expired/Revoked Key Cleanup (every 7 days) ────────────────────────

@tasks.loop(hours=168)  # 7 days
async def weekly_key_cleanup():
    """Weekly: hard-delete all revoked and expired keys from Firestore to keep the DB clean."""
    await bot.wait_until_ready()
    guild = bot.get_guild(GUILD_ID)
    channel = guild.get_channel(MOD_CHANNEL_ID) if guild else None

    try:
        res = await _api("post", "/admin/cleanup-stale-keys", json={})
        deleted  = res.get("deleted_count", 0)
        expired  = res.get("expired_count", 0)
        revoked  = res.get("revoked_count", 0)

        embed = discord.Embed(
            title="🧹  Weekly Key Cleanup Complete",
            description=(
                f"Stale and expired keys have been purged from the database.\n"
                f"**Revoked keys removed:** {revoked}\n"
                f"**Expired keys removed:** {expired}\n"
                f"**Total deleted:** {deleted}"
            ),
            color=0x22D3A5,
        )
        embed.set_footer(text="AuthGuard Cleanup • rivvak.app")
        embed.timestamp = discord.utils.utcnow()
        if channel:
            await channel.send(embed=embed)
        LOG.info(f"Weekly cleanup: {deleted} stale keys removed ({revoked} revoked, {expired} expired)")
    except Exception as e:
        LOG.error(f"Weekly cleanup failed: {e}")
        if channel:
            embed = discord.Embed(
                title="⚠️  Weekly Cleanup Failed",
                description=f"```{e}```",
                color=0xF59E0B,
            )
            await channel.send(embed=embed)

@weekly_key_cleanup.before_loop
async def before_weekly_cleanup():
    await bot.wait_until_ready()


# ── Weekly Key Generation Audit (every 7 days) ───────────────────────────────

@tasks.loop(hours=168)
async def weekly_gen_audit():
    """Weekly: scan recent key generation events for suspicious patterns (bulk creates, multi-account)."""
    await bot.wait_until_ready()
    guild = bot.get_guild(GUILD_ID)
    channel = guild.get_channel(MOD_CHANNEL_ID) if guild else None
    if not channel:
        return

    try:
        # Fetch recent events log
        events_data = await _api_get("/admin/events", limit=200)
        events = events_data.get("events", [])

        # Count generates per Discord user in the last 7 days
        from collections import Counter
        from datetime import timezone as _tz
        gen_counts: Counter = Counter()
        now_ts = discord.utils.utcnow().timestamp()
        week_ago = now_ts - 604800  # 7 days in seconds

        suspicious = []
        seen_ips: dict = {}

        for ev in events:
            if ev.get("event_type") != "generated":
                continue
            ts_raw = ev.get("timestamp") or ev.get("created_at")
            try:
                # Handle both ISO string and epoch
                if isinstance(ts_raw, str):
                    from datetime import datetime as _dt
                    ts = _dt.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp()
                else:
                    ts = float(ts_raw) if ts_raw else 0.0
            except Exception:
                ts = 0.0

            if ts < week_ago:
                continue

            uid = ev.get("data", {}).get("discord_uid") or ev.get("discord_user_id", "unknown")
            gen_counts[uid] += 1

        # Flag any user who generated more than 1 key this week (should be impossible, but double-check)
        flags = [(uid, count) for uid, count in gen_counts.items() if count > 1]
        total_gens = sum(gen_counts.values())

        embed = discord.Embed(
            title="🔍  Weekly Key Generation Audit",
            description=(
                f"**Keys generated in the last 7 days:** {total_gens}\n"
                f"**Suspicious accounts (>1 key):** {len(flags)}"
            ),
            color=0xF59E0B if flags else 0x22D3A5,
        )

        if flags:
            flag_lines = [f"`{uid}` — generated **{count}** keys this week" for uid, count in flags[:10]]
            embed.add_field(
                name="⚠️  Flagged Accounts",
                value="\n".join(flag_lines),
                inline=False,
            )
            embed.add_field(
                name="Recommended Action",
                value="Investigate with `!keyinfo @user`. Use `!ban @user` if abuse confirmed.",
                inline=False,
            )
        else:
            embed.add_field(
                name="✅  No Suspicious Activity",
                value="All key generation patterns look normal.",
                inline=False,
            )

        embed.set_footer(text="AuthGuard Gen Audit • rivvak.app")
        embed.timestamp = discord.utils.utcnow()
        await channel.send(embed=embed)
        LOG.info(f"Weekly gen audit: {total_gens} generates, {len(flags)} flagged")
    except Exception as e:
        LOG.error(f"Weekly gen audit failed: {e}")

@weekly_gen_audit.before_loop
async def before_weekly_gen_audit():
    await bot.wait_until_ready()


# ═══════════════════════════════════════════════════════════════════════════════
# ── UI Views & Modals ─────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

# ── Device reset confirmation view ───────────────────────────────────────────

class DeviceResetView(discord.ui.View):
    """Confirmation buttons for /reset-device."""

    def __init__(self, discord_user_id: str, license_key: str):
        super().__init__(timeout=30)
        self.discord_user_id = discord_user_id
        self.license_key     = license_key

    @discord.ui.button(label="Confirm Reset", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Acknowledge the interaction so edit_original_response() is valid.
        await interaction.response.defer()
        try:
            data = await _api("post", "/device/reset", json={
                "discord_user_id": self.discord_user_id,
                "key": self.license_key,
                "sig": _hmac_sign(f"{self.license_key}:{self.discord_user_id}"),
            })
            if "error" in data:
                err = data["error"]
                cooldown = data.get("cooldown_remaining", "")
                msg = f"❌ Reset failed: {err}"
                if cooldown:
                    msg += f"\nCooldown remaining: **{cooldown}**"
                await interaction.edit_original_response(content=msg, view=None)
            else:
                next_reset = data.get("next_reset_date", "30 days from now")
                await interaction.edit_original_response(
                    content=f"✅ Device binding reset successfully.\nNext reset available: **{next_reset}**",
                    view=None,
                )
        except Exception as e:
            LOG.error(f"Device reset error: {e}")
            await interaction.edit_original_response(
                content="❌ An error occurred while resetting your device. Please try again later.",
                view=None,
            )
        # Disable buttons after action
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Device reset cancelled.", view=None)

    async def on_timeout(self):
        # View expired — disable all buttons silently
        for child in self.children:
            child.disabled = True

# ── Support modal ─────────────────────────────────────────────────────────────

class SupportModal(discord.ui.Modal, title="SteamGuard Support Request"):
    category = discord.ui.TextInput(
        label="Issue Category",
        placeholder="Activation / License / Bug / Discord Sync / Other",
        max_length=50,
    )
    app_version = discord.ui.TextInput(
        label="App Version",
        placeholder="e.g. 1.2.0",
        max_length=20,
    )
    description = discord.ui.TextInput(
        label="Describe your issue",
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Post embed to SUPPORT_CHANNEL_ID if configured
        embed = _embed_info("🎫  New Support Request")
        embed.add_field(name="User",        value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=False)
        embed.add_field(name="Category",    value=self.category.value,    inline=True)
        embed.add_field(name="App Version", value=self.app_version.value, inline=True)
        embed.add_field(name="Description", value=self.description.value, inline=False)
        embed.set_footer(text=f"User ID: {interaction.user.id}")

        if SUPPORT_CHANNEL_ID:
            guild = bot.get_guild(GUILD_ID)
            if guild:
                support_channel = guild.get_channel(SUPPORT_CHANNEL_ID)
                if support_channel:
                    try:
                        await support_channel.send(embed=embed)
                    except Exception as e:
                        LOG.warning(f"Could not post support request to channel: {e}")

        await interaction.response.send_message(
            "✅ Your support request has been submitted. Our team will get back to you soon!",
            ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        LOG.error(f"SupportModal error: {error}")
        await interaction.response.send_message(
            "❌ Failed to submit your request. Please try again later.")

# ═══════════════════════════════════════════════════════════════════════════════
# ── Slash Commands ────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

# ── /status ───────────────────────────────────────────────────────────────────

@bot.command(name="status", help="Check your SteamGuard license and account status")
@in_off_topic()
async def slash_status(ctx: commands.Context):
    try:
        data = await _api_get(f"/admin/key-info/{ctx.author.id}")
    except Exception as e:
        LOG.error(f"/status API error: {e}")
        await ctx.send("❌ Could not reach the license server. Try again later.")
        return

    if "error" in data and not data.get("keys"):
        await ctx.send(
            "❌ Could not fetch your status. You may not have a key yet — use `!getkey` to get one.")
        return

    keys = data.get("keys", [])
    if not keys:
        embed = _embed_info(
            "No License Found",
            "You don't have a SteamGuard license yet. Use `!getkey` to get one.")
        await ctx.send(embed=embed)
        return

    # Use the first (most recent) key for the summary card
    k = keys[0]
    status_str = k.get("status", "unknown")
    status_icon = {"active": "🟢", "paused": "🟡", "revoked": "🔴"}.get(status_str, "⚪")

    _factory = {"active": _embed_success, "paused": _embed_warn, "revoked": _embed_error}.get(status_str, _embed_info)
    embed = _factory(
        f"{status_icon}  Your SteamGuard Status",
        footer="Only visible to you")
    embed.add_field(name="License Tier", value=data.get("tier", k.get("tier", "Standard")), inline=True)
    embed.add_field(name="Status",       value=status_str.upper(),                            inline=True)
    embed.add_field(name="Heal Count",   value=str(data.get("heal_count", k.get("heal_count", 0))), inline=True)
    embed.add_field(name="XP",           value=str(data.get("xp", k.get("xp", 0))),           inline=True)
    embed.add_field(name="Level",        value=str(data.get("level", k.get("level", 1))),      inline=True)
    embed.add_field(name="Badges",       value=str(len(data.get("badges", k.get("badges", [])))), inline=True)

    last_hb = data.get("last_heartbeat", k.get("last_heartbeat", k.get("last_verified", "—")))
    embed.add_field(name="Last Heartbeat", value=str(last_hb), inline=False)

    if k.get("pause_reason"):
        embed.add_field(name="Pause Reason", value=k["pause_reason"], inline=False)

    await ctx.send(embed=embed)

# ── /refer ────────────────────────────────────────────────────────────────────

@bot.command(name="refer", aliases=["referral"], help="Get your personal SteamGuard referral link")
@in_off_topic()
async def slash_refer(ctx: commands.Context):
    try:
        data = await _api("post", "/referral/create", json={
            "discord_user_id": str(ctx.author.id),
            "admin_key":       ADMIN_KEY,
        })
    except Exception as e:
        LOG.error(f"/refer API error: {e}")
        await ctx.send("❌ Could not create referral link. Try again later.")
        return

    if "error" in data:
        await ctx.send(f"❌ {data['error']}")
        return

    referral_code   = data.get("code", "")
    join_link       = data.get("referral_link") or data.get("link", "N/A")
    valid_referrals = data.get("valid_referrals", 0)

    # Mint a real per-user Discord invite so joins can be attributed via
    # on_member_join (Discord strips ?ref= params, so the join URL alone
    # never worked for attribution). Falls back gracefully if the bot
    # lacks Create Instant Invite permission in the target channel.
    discord_invite_url = None
    if referral_code:
        invite_channel = None
        if REFERRAL_INVITE_CHANNEL_ID:
            invite_channel = ctx.guild.get_channel(REFERRAL_INVITE_CHANNEL_ID)
        if invite_channel is None:
            invite_channel = ctx.guild.system_channel
        if invite_channel is None and ctx.guild.text_channels:
            invite_channel = ctx.guild.text_channels[0]

        if invite_channel is not None:
            try:
                invite = await invite_channel.create_invite(
                    max_age=0, max_uses=0, unique=True,
                    reason=f"Referral link for {ctx.author}",
                )
                await _api("post", "/referral/link-invite", json={
                    "code":                referral_code,
                    "discord_invite_code": invite.code,
                    "admin_key":           ADMIN_KEY,
                })
                discord_invite_url = invite.url
            except discord.Forbidden:
                LOG.warning(
                    f"Missing 'Create Instant Invite' permission — could not "
                    f"mint a tracked invite for {ctx.author}; falling back to join URL.")
            except Exception as e:
                LOG.warning(f"Failed to create/link referral invite for {ctx.author}: {e}")

    primary_link = discord_invite_url or join_link

    embed = _embed_info(
        "🔗  Your SteamGuard Referral Link",
        footer="Only visible to you")
    embed.add_field(name="Your Link",         value=primary_link,             inline=False)
    if discord_invite_url:
        embed.add_field(name="Alt Link (join page)", value=join_link,         inline=False)
    embed.add_field(name="Valid Referrals",   value=str(valid_referrals),     inline=True)
    embed.add_field(name="​",                 value="​",                       inline=True)
    embed.add_field(
        name="Reward Tiers",
        value=(
            "**1 valid** → Badge unlock\n"
            "**3 valid** → Theme pack\n"
            "**5 valid** → Founder entry\n"
            "**10 valid** → 1 month premium"
        ),
        inline=False,
    )
    await ctx.send(embed=embed)

# ── /stats ────────────────────────────────────────────────────────────────────

@bot.command(name="stats", help="View your SteamGuard protection statistics")
@in_off_topic()
async def slash_stats(ctx: commands.Context):
    try:
        key_data   = await _api_get(f"/admin/key-info/{ctx.author.id}")
        badge_data = await _api_get(f"/badges/{ctx.author.id}")
    except Exception as e:
        LOG.error(f"/stats API error: {e}")
        await ctx.send("❌ Could not fetch your stats. Try again later.")
        return

    if "error" in key_data and not key_data.get("keys"):
        await ctx.send(
            "❌ No license found. Use `!getkey` to get started.")
        return

    xp          = key_data.get("xp", 0)
    level       = key_data.get("level", 1)
    total_heals = key_data.get("heal_count", 0)
    total_kills = key_data.get("kill_count", 0)
    badges      = badge_data.get("badges", key_data.get("badges", []))
    last_active = key_data.get("last_heartbeat", key_data.get("last_verified", "—"))

    # Streak now lives in the rewards Firestore doc — source it from
    # /rewards/status so /stats reflects the authoritative check-in streak.
    streak = "—"
    try:
        rstatus = await _api_get(f"/rewards/status/{ctx.author.id}")
        if isinstance(rstatus, dict) and "error" not in rstatus:
            cs = int(rstatus.get("current_streak", 0) or 0)
            streak = f"🔥 {cs} day{'s' if cs != 1 else ''}" if cs >= 3 else f"{cs} day{'s' if cs != 1 else ''}"
    except Exception as e:
        LOG.warning(f"/stats streak fetch failed: {e}")
        streak = badge_data.get("current_streak", key_data.get("current_streak", "—"))

    embed = _embed(
        f"📊  {ctx.author.display_name}'s SteamGuard Stats",
        footer="Use /refer to earn rewards")
    embed.set_thumbnail(url=ctx.author.display_avatar.url)
    embed.add_field(name="⭐ XP",           value=f"{xp:,}",         inline=True)
    embed.add_field(name="🏆 Level",        value=str(level),         inline=True)
    embed.add_field(name="​",               value="​",                 inline=True)
    embed.add_field(name="🛡 Total Heals",  value=f"{total_heals:,}", inline=True)
    embed.add_field(name="💀 Total Kills",  value=f"{total_kills:,}", inline=True)
    embed.add_field(name="🔥 Streak",       value=str(streak),        inline=True)
    embed.add_field(
        name=f"🎖 Badges ({len(badges)})",
        value=", ".join(badges) if badges else "None yet",
        inline=False,
    )
    embed.add_field(name="🕒 Last Active",  value=str(last_active), inline=False)
    await ctx.send(embed=embed)

# ── /leaderboard ──────────────────────────────────────────────────────────────

@bot.command(name="leaderboard", help="View the weekly SteamGuard leaderboard")
@in_off_topic()
async def slash_leaderboard(ctx: commands.Context):
    try:
        data = await _api_get("/leaderboard")
    except Exception as e:
        LOG.error(f"/leaderboard API error: {e}")
        await ctx.send("❌ Could not fetch leaderboard. Try again later.")
        return

    if "error" in data:
        await ctx.send(f"❌ {data['error']}")
        return

    entries = data.get("entries", data.get("leaderboard", []))[:10]

    embed = _embed(
        "🏆  Weekly SteamGuard Leaderboard",
        footer="Resets every Monday at midnight UTC")

    if not entries:
        embed.description = "No leaderboard data available yet. Keep using SteamGuard!"
    else:
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, entry in enumerate(entries):
            rank     = medals[i] if i < 3 else f"**#{i+1}**"
            user_id  = entry.get("discord_user_id")
            username = entry.get("username", entry.get("display_name", "Anonymous"))
            heals    = entry.get("heal_count", entry.get("heals", 0))
            kills    = entry.get("kill_count", entry.get("kills", 0))
            xp       = entry.get("xp", 0)

            if user_id:
                user_str = f"<@{user_id}>"
            else:
                user_str = f"`{username}`"

            lines.append(f"{rank} {user_str} — 🛡 {heals:,} heals  💀 {kills:,} kills  ⭐ {xp:,} XP")

        embed.description = "\n".join(lines)

    await ctx.send(embed=embed)

# ── /reset-device ─────────────────────────────────────────────────────────────

@bot.command(name="resetdevice", help="")
@in_off_topic()
async def slash_reset_device(ctx: commands.Context):

    # Fetch the user's key first
    try:
        data = await _api_get(f"/admin/key-info/{ctx.author.id}")
    except Exception as e:
        LOG.error(f"/reset-device key fetch error: {e}")
        await ctx.send("❌ Could not reach the license server. Try again later.")
        return

    keys = data.get("keys", [])
    active_keys = [k for k in keys if k.get("status") == "active"]

    if not active_keys:
        await ctx.send(
            "❌ You don't have an active license key to reset.")
        return

    license_key = active_keys[0].get("key_hash", "")

    embed = _embed_warn(
        "Confirm Device Reset",
        (
            "This will **clear your hardware binding**, allowing you to activate on a new device.\n\n"
            "**Note:** You can only reset once every **30 days**.\n\n"
            "Are you sure you want to proceed?"
        ),
        footer="This confirmation expires in 30 seconds")

    view = DeviceResetView(
        discord_user_id=str(ctx.author.id),
        license_key=license_key,
    )
    await ctx.send(embed=embed, view=view)

# ── /download ─────────────────────────────────────────────────────────────────

@bot.command(name="download", help="Get the latest SteamGuard installer")
@in_off_topic()
async def slash_download(ctx: commands.Context):

    version = "latest"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                "https://raw.githubusercontent.com/rivvak/SteamGuard/main/version.txt"
            )
            if r.status_code == 200:
                version = r.text.strip()
    except Exception as e:
        LOG.warning(f"/download version fetch error: {e}")

    embed = _embed_info(
        "⬇️  SteamGuard Download",
        (
            f"**Version:** `{version}`\n\n"
            f"📥  **[Download SteamGuard]({UPDATE_DOWNLOAD_URL})**\n\n"
            "🔐  Always verify the **SHA-256 signature** before running any executable.\n"
            "The official hash is posted in the `#announcements` channel after each release."
        ),
        footer=f"v{version} • Only visible to you")
    embed.add_field(
        name="⚠️  Safety reminder",
        value=(
            "• Only download from the official link above\n"
            "• Never run files sent to you in DMs\n"
            "• Check the hash before executing"
        ),
        inline=False,
    )
    await ctx.send(embed=embed)

# ── /vote ─────────────────────────────────────────────────────────────────────

@bot.command(name="vote", help="Vote on upcoming SteamGuard features")
@in_off_topic()
async def slash_vote(ctx: commands.Context):
    number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

    embed = _embed(
        "🗳️  SteamGuard Feature Vote",
        (
            "React to this message with the number of the feature you want most!\n"
            "You can vote for multiple features.\n\n"
        ),
        footer="Your feedback shapes the roadmap")

    topic_lines = []
    for i, topic in enumerate(VOTE_TOPICS[:10]):
        emoji = number_emojis[i] if i < len(number_emojis) else f"{i+1}."
        topic_lines.append(f"{emoji}  {topic}")

    embed.description += "\n".join(topic_lines)

    sent_message = await ctx.send(embed=embed)

    # Add reaction prompts to the sent message
    for i in range(min(len(VOTE_TOPICS), 10)):
        try:
            await sent_message.add_reaction(number_emojis[i])
        except Exception:
            pass

# ── /support ──────────────────────────────────────────────────────────────────

class SupportButtonView(discord.ui.View):
    """Prefix commands can't open a modal directly (modals need an Interaction),
    so we surface a button whose callback opens the SupportModal."""
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="Open Support Form", style=discord.ButtonStyle.primary)
    async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SupportModal())

@bot.command(name="support", help="Submit a support request")
@in_off_topic()
async def slash_support(ctx: commands.Context):
    await ctx.send(
        "Click the button below to open the support form:",
        view=SupportButtonView())

# ── /checkbadges ──────────────────────────────────────────────────────────────

@bot.command(name="checkbadges", help="Check if you earned any new badges and announce them")
@in_off_topic()
async def slash_checkbadges(ctx: commands.Context):

    try:
        data = await _api_get(f"/badges/{ctx.author.id}")
    except Exception as e:
        LOG.error(f"/checkbadges API error: {e}")
        await ctx.send("❌ Could not fetch badge data. Try again later.")
        return

    if "error" in data:
        await ctx.send(f"❌ {data['error']}")
        return

    new_badges = data.get("new_badges", [])
    all_badges = data.get("badges", [])

    if not new_badges:
        embed = _embed_info(
            "🎖  Badge Check",
            (
                f"No new badges since last check.\n\n"
                f"You have **{len(all_badges)}** badge(s) total: "
                + (", ".join(all_badges) if all_badges else "none yet")
            ))
        await ctx.send(embed=embed)
        return

    # Announce new badges in badge channel if configured
    guild = bot.get_guild(GUILD_ID)
    if guild and BADGE_ANNOUNCE_CHANNEL_ID:
        announce_channel = guild.get_channel(BADGE_ANNOUNCE_CHANNEL_ID)
        if announce_channel:
            announce_embed = _embed_success(
                "New Badge Unlocked!",
                (
                    f"{ctx.author.mention} just earned "
                    + (", ".join(f"**{b}**" for b in new_badges))
                    + "!"
                ),
                footer="Keep protecting to earn more")
            announce_embed.set_thumbnail(url=ctx.author.display_avatar.url)
            try:
                await announce_channel.send(embed=announce_embed)
            except Exception as e:
                LOG.warning(f"Could not post badge announcement: {e}")

    confirm_embed = _embed_success(
        "New Badge(s) Earned!",
        (
            "You unlocked: " + ", ".join(f"**{b}**" for b in new_badges) + "\n\n"
            "An announcement has been posted in the server. Congrats!"
        ))
    await ctx.send(embed=confirm_embed)

# ── /rewards ──────────────────────────────────────────────────────────────────

@bot.command(name="rewards", help="See all ways to earn free SteamGuard time")
@in_off_topic()
async def cmd_rewards(ctx: commands.Context):
    uid = str(ctx.author.id)
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(f"{LICENSE_SERVER_URL}/rewards/status/{uid}")
            status = resp.json() if resp.status_code == 200 else {}
    except Exception:
        status = {}
    is_owner  = status.get("is_owner", False)
    total_hrs = status.get("total_reward_hours", 0.0)
    triggers  = status.get("triggers", {})
    streak    = int(status.get("current_streak", 0) or 0)
    longest   = int(status.get("longest_streak", 0) or 0)

    def _fmt_cooldown(minutes):
        minutes = int(minutes or 0)
        h, m = divmod(minutes, 60)
        return f"{h}h {m}m" if h else f"{m}m"

    embed = _embed_info(
        "\u23f0  SteamGuard Rewards",
        ("Earn free hours by completing these actions."
         if not is_owner else
         "\u267e\ufe0f  You have unlimited access as an owner."),
        footer="Use !invitefriends to share your referral link")
    if not is_owner:
        embed.add_field(name="\U0001f381  Total Earned",
                        value=f"**{total_hrs:.1f}h** bonus time", inline=True)
        flame = "\U0001f525" if streak >= 3 else "\U0001f4c5"
        streak_val = f"{flame} **{streak}** day{'s' if streak != 1 else ''} in a row"
        if streak > 0 and streak % 7 != 0:
            streak_val += f"\n*{7 - (streak % 7)} more for +5h*"
        if longest:
            streak_val += f"\n*Best: {longest}d*"
        embed.add_field(name="\U0001f525  Check-in Streak", value=streak_val, inline=True)
    for trigger, (label, reward_str, description) in REWARD_MENU.items():
        t_data = triggers.get(trigger, {})
        count  = t_data.get("times_claimed", 0)
        mx     = t_data.get("max_per_user", -1)
        # Prefer the explicit flags the server now sends.
        maxed       = t_data.get("maxed", (mx != -1 and count >= mx))
        on_cooldown = t_data.get("on_cooldown", bool(t_data.get("next_available")))
        mins_left   = t_data.get("minutes_until_available")
        if maxed:
            avail = "\u2705 Claimed (max)"
        elif on_cooldown:
            avail = (f"\u23f3 Cooldown — {_fmt_cooldown(mins_left)} left"
                     if mins_left is not None else "\u23f3 Cooldown active")
        else:
            avail = "\u2705 Available"
        embed.add_field(name=f"{reward_str}  \u2014  {label}",
                        value=f"{description}\n*{avail}* (claimed {count}x)", inline=False)
    await ctx.send(embed=embed)

# ── /invite-friends ───────────────────────────────────────────────────────────

@bot.command(name="invitefriends", help="Get your referral link — earn +3h for each friend who activates")
@in_off_topic()
async def cmd_invite_friends(ctx: commands.Context):
    uid = str(ctx.author.id)
    if ctx.author.id in OWNER_DISCORD_IDS:
        await ctx.send(
            "\u267e\ufe0f Owner account \u2014 you have unlimited access.")
        return
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(f"{LICENSE_SERVER_URL}/referral/create",
                json={"discord_user_id": uid, "admin_key": ADMIN_KEY})
        data = resp.json()
    except Exception as e:
        await ctx.send(f"\u274c Could not create referral link: {e}")
        return
    referral_link   = data.get("referral_link", data.get("link", "N/A"))
    valid_referrals = data.get("valid_referrals", 0)
    pending         = data.get("pending_referrals", 0)
    earned_hours    = valid_referrals * 3.0
    invite_msg = (
        f"\U0001f6e1\ufe0f Hey! I've been using **SteamGuard** \u2014 a free tool from Rivvak Community "
        f"that keeps you in Steam games when family sharing kicks you out.\n\n"
        f"\U0001f449 Join the server: **{DISCORD_INVITE}**\n"
        f"\U0001f511 Use my referral code to get started: **{referral_link}**\n\n"
        f"It's free, takes 2 minutes to set up, and actually works."
    )
    embed = _embed(
        "\U0001f517  Invite Friends \u2014 Earn +3h Each",
        (
            f"For every friend who joins **and activates SteamGuard**, "
            f"you earn **+3 free hours** of access.\n\n"
            f"Share your link or copy the pre-written message below."
        ),
        footer="Credits apply after your friend activates their key")
    embed.add_field(name="\U0001f517  Your Referral Link",  value=f"`{referral_link}`",   inline=False)
    embed.add_field(name="\u2705  Valid Referrals",         value=str(valid_referrals),   inline=True)
    embed.add_field(name="\u23f3  Pending",                 value=str(pending),           inline=True)
    embed.add_field(name="\u23f0  Total Earned",            value=f"{earned_hours:.0f}h", inline=True)
    embed.add_field(name="\U0001f4cb  Copy this message to DM friends:",
                    value=f"```\n{invite_msg}\n```", inline=False)
    await ctx.send(embed=embed)

# ── /daily ────────────────────────────────────────────────────────────────────

@bot.command(name="daily", aliases=["check-in", "checkin"], help="Claim your daily check-in reward")
@in_off_topic()
async def cmd_daily(ctx: commands.Context):
    uid = str(ctx.author.id)
    if ctx.author.id in OWNER_DISCORD_IDS:
        await ctx.send("\u267e\ufe0f Owner \u2014 unlimited access.")
        return
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                f"{LICENSE_SERVER_URL}/rewards/grant",
                json={"discord_user_id": uid, "trigger": "daily_check_in"},
                headers={"x-admin-key": ADMIN_KEY},
            )
        result = resp.json()
    except Exception as e:
        await ctx.send(f"\u274c Error: {e}")
        return
    if result.get("granted"):
        streak = int(result.get("current_streak", 0) or 0)
        hours  = result.get("hours", 0.5) or 0.5
        gained = (f"+{int(round(hours * 60))} minutes" if hours < 1
                  else f"+{hours:g}h")
        desc = f"\U0001f552 **{gained}** added to your SteamGuard access."

        # Streak feedback.
        if streak > 0:
            flame = "\U0001f525" if streak >= 3 else "\U0001f4c5"
            desc += f"\n\n{flame} **{streak}-day** check-in streak!"
            remaining = (7 - (streak % 7)) % 7
            if result.get("streak_bonus", {}).get("granted"):
                bonus_h = result["streak_bonus"].get("hours", 5)
                desc += (f"\n\U0001f389 7-day streak reached — "
                         f"**+{bonus_h:g}h** streak bonus awarded!")
            elif remaining:
                desc += f"\n*{remaining} more day{'s' if remaining != 1 else ''} until your +5h streak bonus.*"

        total = result.get("new_total_hours")
        footer = (f"Total bonus: {total:.1f}h · Come back tomorrow"
                  if isinstance(total, (int, float))
                  else "Come back tomorrow for another reward")
        embed = _embed_success("Daily Reward Claimed!", desc, footer=footer)
    else:
        embed = _embed_warn("Already Claimed",
                            result.get("reason", "Try again later."))
    await ctx.send(embed=embed)

# ── /grant-reward (admin) ─────────────────────────────────────────────────────

@bot.command(name="grantreward", help="[Admin] Manually grant a reward to a user")
@in_off_topic()
@discord.app_commands.describe(user="Discord user to reward",
                                trigger="Reward trigger (e.g. bug_report)",
                                override="Bypass cooldown/cap?")
async def cmd_grant_reward(ctx: commands.Context, user: discord.Member, trigger: str, override: bool = False):
    if ctx.author.id not in ADMIN_USER_IDS:
        await ctx.send("\u274c Admin only.")
        return
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(f"{LICENSE_SERVER_URL}/rewards/grant",
                json={"discord_user_id": str(user.id), "trigger": trigger, "admin_override": override},
                headers={"x-admin-key": ADMIN_KEY})
        result = resp.json()
    except Exception as e:
        await ctx.send(f"\u274c Error: {e}")
        return
    if result.get("granted"):
        embed = _embed_success(
            "Reward Granted",
            (f"Gave **{user.display_name}** {result['reason']}\n"
             f"Total hours now: **{result.get('new_total_hours', 0):.1f}h**"))
        try:
            await user.send(f"\U0001f381 You received a SteamGuard reward from an admin!\n"
                            f"{result['reason']}\nYour total: {result.get('new_total_hours', 0):.1f}h")
        except Exception:
            pass
    else:
        embed = _embed_error("Not Granted",
                             result.get("reason", "Unknown"))
    await ctx.send(embed=embed)

# ── Slash command error handler ───────────────────────────────────────────────


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError) -> None:
    LOG.exception("Unhandled slash command error: %s", error)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                embed=_embed_error("Command Error", f"`{error}`"),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=_embed_error("Command Error", f"`{error}`"),
                ephemeral=True,
            )
    except discord.HTTPException:
        pass  # interaction token expired; nothing we can do


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        bot.run(BOT_TOKEN)
    except Exception:
        LOG.exception("bot.run() exited with unhandled exception — Cloud Run will restart the container")
        raise
