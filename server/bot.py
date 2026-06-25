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
  MEMBERS_CHANNEL_ID  — voice channel to rename with member count
  ACTIVE_CHANNEL_ID   — voice channel to rename with active key count
  HEALS_CHANNEL_ID    — voice channel to rename with total heals count
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
import httpx
import discord
import discord.app_commands
from discord.ext import commands, tasks
from datetime import datetime, timezone

LOG = logging.getLogger("sg-bot")
logging.basicConfig(level=logging.INFO)

# ── Env vars (original) ───────────────────────────────────────────────────────

GUILD_ID           = int(os.environ["DISCORD_GUILD_ID"])
ROLE_ID            = int(os.environ["DISCORD_ROLE_ID"])
GETKEY_CHANNEL_ID    = int(os.environ.get("GETKEY_CHANNEL_ID", "0"))
OFF_TOPIC_CHANNEL_ID = int(os.environ.get("OFF_TOPIC_CHANNEL_ID", "1513193890117714182"))
LICENSE_SERVER_URL = os.environ["LICENSE_SERVER_URL"]
ADMIN_KEY          = os.environ["ADMIN_KEY"]
BOT_TOKEN          = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_INVITE     = os.environ.get("DISCORD_INVITE", "https://discord.gg/REPLACE")

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

MEMBERS_CHANNEL_ID          = int(os.environ.get("MEMBERS_CHANNEL_ID", "0") or "0")
ACTIVE_CHANNEL_ID           = int(os.environ.get("ACTIVE_CHANNEL_ID", "0") or "0")
HEALS_CHANNEL_ID            = int(os.environ.get("HEALS_CHANNEL_ID", "0") or "0")
SUPPORT_CHANNEL_ID          = int(os.environ.get("SUPPORT_CHANNEL_ID", "0") or "0")
BADGE_ANNOUNCE_CHANNEL_ID   = int(os.environ.get("BADGE_ANNOUNCE_CHANNEL_ID", "0") or "0")
UPDATE_DOWNLOAD_URL         = os.environ.get(
    "UPDATE_DOWNLOAD_URL", "https://github.com/rivvak/SteamGuard/releases/latest"
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
_C_BRAND   = 0x00C46A   # Default/neutral embeds
_C_SUCCESS = 0x2ECC71   # Key redeemed, reward claimed
_C_ERROR   = 0xED4245   # Failed redemption, invalid key
_C_WARN    = 0xF1C40F   # Cooldowns, confirmations
_C_INFO    = 0x3498DB   # Help, status, neutral info

def _embed(title: str, description: str = "", color: int = _C_BRAND,
           fields: list = None, footer: str = None) -> discord.Embed:
    """Base embed with consistent AuthGuard branding."""
    e = discord.Embed(title=title, description=description, color=color)
    e.set_author(name="AuthGuard", icon_url="https://cdn.discordapp.com/embed/avatars/0.png")
    e.set_footer(text=f"AuthGuard • Rivvak Community{(' | ' + footer) if footer else ''}")
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

bot = commands.Bot(command_prefix="!", intents=intents)


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
    update_stat_channels.start()
    # Sync slash commands to the guild
    try:
        guild_obj = discord.Object(id=GUILD_ID)
        synced = await bot.tree.sync(guild=guild_obj)
        LOG.info(f"Synced {len(synced)} slash command(s) to guild {GUILD_ID}")
    except Exception as e:
        LOG.error(f"Failed to sync slash commands: {e}")

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

@bot.command(name="mykey")
@in_getkey_channel()
async def my_key(ctx: commands.Context):
    """Show the user their own key status."""
    try:
        await ctx.message.delete()
    except Exception:
        pass

    data = await _api_get(f"/admin/key-info/{ctx.author.id}")
    keys = data.get("keys", [])

    if not keys:
        embed = _embed_info(
            "No Key Found",
            "You don't have a key yet. Use `!getkey` to get one.")
        await ctx.author.send(embed=embed)
        await ctx.send(f"📬 {ctx.author.mention} Check your DMs.", delete_after=6)
        return

    embed = _embed_info(
        "Your Key Status",
        "🔒 **For security, delete this message after reading.**",
        footer="Keys are verified every 24h")
    for k in keys:
        status_icon = {"active": "🟢", "paused": "🟡", "revoked": "🔴"}.get(k["status"], "⚪")
        embed.add_field(
            name=f"{status_icon} Key `{k['key_hash']}`",
            value=(
                f"**Status:** {k['status'].upper()}\n"
                f"**Machine bound:** {'Yes' if k['hwid_bound'] else 'No (not activated yet)'}\n"
                f"**Running since:** {k.get('running_since', 'not activated')}\n"
                f"**Last verified:** {k.get('last_verified', 'never')}\n"
                f"**Verify count:** {k.get('verify_count', 0)}\n"
                + (f"**Paused reason:** {k['pause_reason']}\n" if k.get('pause_reason') else "")
            ),
            inline=False)

    try:
        await ctx.author.send(embed=embed)
        await ctx.send(f"📬 {ctx.author.mention} Check your DMs.", delete_after=6)
    except discord.Forbidden:
        await ctx.send(embed=embed, delete_after=30)

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

# ── Admin: !listkeys ──────────────────────────────────────────────────────────

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
            f"Run `!commandhelp` for all commands.",
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


# ── !commandhelp ──────────────────────────────────────────────────────────────

@bot.command(name="commandhelp", aliases=["commands", "cmds"])
@in_off_topic()
async def cmd_commandhelp(ctx: commands.Context):
    """Lists all available SteamGuard bot commands."""
    is_adm = (ctx.author.id in ADMIN_USER_IDS
              or (ctx.guild and ctx.author.guild_permissions.administrator))

    getkey_ch = ctx.guild.get_channel(GETKEY_CHANNEL_ID) if ctx.guild and GETKEY_CHANNEL_ID else None
    ot_ch     = ctx.guild.get_channel(OFF_TOPIC_CHANNEL_ID) if ctx.guild and OFF_TOPIC_CHANNEL_ID else None
    getkey_ref = getkey_ch.mention if getkey_ch else "#get-key"
    ot_ref     = ot_ch.mention if ot_ch else "#-off-topic"

    embed = _embed_info(
        "🛡  SteamGuard — All Commands",
        (
            f"Use commands in the correct channel.\n"
            f"Key commands → {getkey_ref} | Everything else → {ot_ref}"
        ),
        footer="discord.gg/RTHM8YhpE",
    )

    # ── Key Commands ──
    embed.add_field(
        name=f"🔑  Key Commands ({getkey_ref} only)",
        value=(
            "`!getkey` — Get a SteamGuard license key\n"
            "`!mykey` — Check your key status & expiry\n"
            "`!linkyoutube` — Link your YouTube account for verification"
        ),
        inline=False,
    )

    # ── Account Commands ──
    embed.add_field(
        name=f"👤  Account Commands ({ot_ref})",
        value=(
            "`!status` — View your license, XP, badges & tier\n"
            "`!stats` — Show your public protection stats card\n"
            "`!refer` — Get your personal referral link\n"
            "`!resetdevice` — Reset your HWID (30-day cooldown)"
        ),
        inline=False,
    )

    # ── Rewards ──
    embed.add_field(
        name="🎁  Reward Commands",
        value=(
            "`!rewards` — See all ways to earn free time\n"
            "`!daily` — Claim your daily +30 min bonus (once per ~20h)\n"
            "`!invitefriends` — Get your referral link + invite message (+3h per friend)"
        ),
        inline=False,
    )

    # ── Community ──
    embed.add_field(
        name="🌐  Community Commands",
        value=(
            "`!leaderboard` — Weekly top-10 heals leaderboard\n"
            "`!vote` — Vote on upcoming features\n"
            "`!download` — Get the latest SteamGuard installer\n"
            "`!support` — Submit a support ticket\n"
            "`!checkbadges` — Check for any new badge unlocks"
        ),
        inline=False,
    )

    # ── Admin Commands (only shown to admins) ──
    if is_adm:
        embed.add_field(
            name="⚙️  Admin Commands",
            value=(
                "`!listkeys` — List all license keys\n"
                "`!keyinfo @user` — Detailed info on a user's key\n"
                "`!pausekey <key>` — Suspend a key\n"
                "`!unpausekey <key>` — Unsuspend a key\n"
                "`!revokekey <key>` — Permanently revoke a key\n"
                "`!grantreward @user <trigger>` — Manually grant a reward\n"
                "`!sgstatus` — Live server & bot health stats"
            ),
            inline=False,
        )

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

# ── Auto-updating stat channels ───────────────────────────────────────────────

@tasks.loop(minutes=10)
async def update_stat_channels():
    """Rename voice channels with live stats from /stats/server."""
    try:
        stats = await _api_get("/stats/server")
    except Exception:
        return

    if "error" in stats:
        return

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    total_keys  = stats.get("total_keys", 0)
    active_keys = stats.get("active_keys", 0)
    total_heals = stats.get("total_heals", 0)

    channel_updates = [
        (MEMBERS_CHANNEL_ID, f"👥 Members: {total_keys:,}"),
        (ACTIVE_CHANNEL_ID,  f"✅ Active: {active_keys:,}"),
        (HEALS_CHANNEL_ID,   f"🛡 Heals: {total_heals:,}"),
    ]

    for ch_id, new_name in channel_updates:
        if ch_id == 0:
            continue
        channel = guild.get_channel(ch_id)
        if channel is None:
            continue
        try:
            if channel.name != new_name:
                await channel.edit(name=new_name)
                LOG.info(f"Stat channel updated: {new_name}")
        except Exception as e:
            LOG.warning(f"Could not update stat channel {ch_id}: {e}")

@update_stat_channels.before_loop
async def before_stat_channels():
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
    async def confirm(self, ctx: commands.Context, button: discord.ui.Button):
        try:
            data = await _api("post", "/device/reset", json={
                "discord_user_id": self.discord_user_id,
                "key": self.license_key,
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
    async def cancel(self, ctx: commands.Context, button: discord.ui.Button):
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
        embed.add_field(name="User",        value=f"{ctx.author.mention} (`{ctx.author.id}`)", inline=False)
        embed.add_field(name="Category",    value=self.category.value,    inline=True)
        embed.add_field(name="App Version", value=self.app_version.value, inline=True)
        embed.add_field(name="Description", value=self.description.value, inline=False)
        embed.set_footer(text=f"User ID: {ctx.author.id}")

        if SUPPORT_CHANNEL_ID:
            guild = bot.get_guild(GUILD_ID)
            if guild:
                support_channel = guild.get_channel(SUPPORT_CHANNEL_ID)
                if support_channel:
                    try:
                        await support_channel.send(embed=embed)
                    except Exception as e:
                        LOG.warning(f"Could not post support request to channel: {e}")

        await ctx.send(
            "✅ Your support request has been submitted. Our team will get back to you soon!")

    async def on_error(self, ctx: commands.Context, error: Exception):
        LOG.error(f"SupportModal error: {error}")
        await ctx.send(
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

@bot.command(name="refer", help="Get your personal SteamGuard referral link")
@in_off_topic()
async def slash_refer(ctx: commands.Context):
    try:
        data = await _api("post", "/referral/create", json={
            "discord_user_id": str(ctx.author.id),
        })
    except Exception as e:
        LOG.error(f"/refer API error: {e}")
        await ctx.send("❌ Could not create referral link. Try again later.")
        return

    if "error" in data:
        await ctx.send(f"❌ {data['error']}")
        return

    referral_link   = data.get("referral_link", "N/A")
    valid_referrals = data.get("valid_referrals", 0)

    embed = _embed_info(
        "🔗  Your SteamGuard Referral Link",
        footer="Only visible to you")
    embed.add_field(name="Your Link",         value=referral_link,           inline=False)
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

    # Try to get streak from badge data
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

    await ctx.send(embed=embed, ephemeral=False)

    # Add reaction prompts to the sent message
    sent_message = await interaction.original_response()
    for i in range(min(len(VOTE_TOPICS), 10)):
        try:
            await sent_message.add_reaction(number_emojis[i])
        except Exception:
            pass

# ── /support ──────────────────────────────────────────────────────────────────

@bot.command(name="support", help="Submit a support request")
@in_off_topic()
async def slash_support(ctx: commands.Context):
    await interaction.response.send_modal(SupportModal())

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
    embed = _embed_info(
        "\u23f0  SteamGuard Rewards",
        ("Earn free hours by completing these actions."
         if not is_owner else
         "\u267e\ufe0f  You have unlimited access as an owner."),
        footer="Use !invitefriends to share your referral link")
    if not is_owner:
        embed.add_field(name="\U0001f381  Total Earned",
                        value=f"**{total_hrs:.1f}h** bonus time", inline=False)
    for trigger, (label, reward_str, description) in REWARD_MENU.items():
        t_data = triggers.get(trigger, {})
        count  = t_data.get("times_claimed", 0)
        next_a = t_data.get("next_available")
        mx     = t_data.get("max_per_user", -1)
        if next_a:        avail = "\u23f3 Cooldown active"
        elif mx != -1 and count >= mx: avail = "\u2705 Claimed"
        else:             avail = "\u2705 Available"
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

@bot.command(name="daily", help="")
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
        embed = _embed_success("Daily Reward Claimed!",
                               "\U0001f552 **+30 minutes** added to your SteamGuard access.",
                               footer="Come back tomorrow for another reward")
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

# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
