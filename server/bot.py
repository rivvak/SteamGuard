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

# ── Bot setup ─────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.members         = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


async def check_slash_channel(interaction: discord.Interaction) -> bool:
    """Global slash check: slash commands only work in #-off-topic (admins exempt)."""
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Please use commands in the Rivvak Community server.", ephemeral=True)
        return False
    user = interaction.user
    is_adm = (user.id in ADMIN_USER_IDS
              or (hasattr(user, "guild_permissions")
                  and user.guild_permissions.administrator))
    if is_adm:
        return True
    if OFF_TOPIC_CHANNEL_ID != 0 and interaction.channel_id != OFF_TOPIC_CHANNEL_ID:
        ch = interaction.guild.get_channel(OFF_TOPIC_CHANNEL_ID)
        ref = ch.mention if ch else "#-off-topic"
        await interaction.response.send_message(
            f"❌ Please use slash commands in {ref}.", ephemeral=True)
        return False
    return True


bot.tree.interaction_check = check_slash_channel


# ── Channel guard decorators ───────────────────────────────────────────────────

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
        embed = discord.Embed(
            title="⚠️  I don't accept DMs",
            description=(
                "All SteamGuard commands must be used in our Discord server.\n\n"
                f"👉  **[Click here to join]({DISCORD_INVITE})**\n\n"
                "Then use `!getkey` in the **#get-key** channel."
            ),
            color=C_YELLOW)
        embed.set_footer(text="SteamGuard Bot")
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
    embed = discord.Embed(
        title="🛡  Welcome to SteamGuard!",
        description=(
            "Here's how to get started:\n\n"
            "1. ✅  Complete verification in **#verify**\n"
            "2. ⬇️  Download the app: `/download`\n"
            "3. 🔑  Activate your license in the app\n"
            "4. 📊  Run `/stats` to see your first card\n"
            "5. 🔗  Use `/refer` to earn rewards\n\n"
            "Need help? Use `/support` anytime."
        ),
        color=C_BLUE,
    )
    embed.set_footer(text="SteamGuard • Your protection starts now")
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

    embed = discord.Embed(
        title="🔑  SteamGuard — Get Your License Key",
        color=C_BLUE)
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
    embed.set_footer(text="Your key is locked to your machine. Do not share it.")

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
        embed = discord.Embed(
            title="❌  Missing Member Role",
            description=(
                "You need the **Member** role to get a key.\n\n"
                "Subscribe to our YouTube channel and make sure you're verified."
            ),
            color=C_RED)
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

    embed = discord.Embed(title="🔑  Your SteamGuard License Key", color=C_BLUE)
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
    embed.set_footer(text="SteamGuard • Valid while you remain a Member + YouTube subscriber")

    try:
        await ctx.author.send(embed=embed)
        confirm = discord.Embed(
            description=f"✅ {ctx.author.mention} Key sent to your DMs!",
            color=C_GREEN)
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
        embed = discord.Embed(
            title="🔑  No Key Found",
            description="You don't have a key yet. Use `!getkey` to get one.",
            color=C_GREY)
        await ctx.author.send(embed=embed)
        await ctx.send(f"📬 {ctx.author.mention} Check your DMs.", delete_after=6)
        return

    embed = discord.Embed(title=f"🔑  Your Key Status", color=C_BLUE)
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

    embed.set_footer(text="SteamGuard • Keys are verified every 24h")
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

    embed = discord.Embed(
        title="🎥  Link Your YouTube Account",
        description=(
            "Click the link below to authorize SteamGuard to verify your YouTube subscription.\n\n"
            f"**[Click here to link YouTube]({auth_url})**\n\n"
            "This only checks if you're subscribed — we cannot see, edit, or delete anything."
        ),
        color=C_RED)
    embed.set_footer(text="Link expires in 10 minutes")

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

    embed = discord.Embed(
        title=f"🔑  Keys for {member.display_name}",
        color=C_BLUE)

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
    embed = discord.Embed(
        title="⏸  Key Paused",
        description=f"Paused **{count}** key(s) for {member.mention}\nReason: `{reason}`",
        color=C_YELLOW)
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
    embed = discord.Embed(
        title="🔴  Key Revoked",
        description=f"Permanently revoked **{count}** key(s) for {member.mention}\nReason: `{reason}`",
        color=C_RED)
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

    embed = discord.Embed(
        title="SteamGuard System Status",
        color=C_GREEN if server_ok else C_RED)
    embed.add_field(name="Bot",         value="🟢 Online",                                   inline=True)
    embed.add_field(name="Server",      value=f"{'🟢 Online' if server_ok else '🔴 Down'}",   inline=True)
    embed.add_field(name="Server time", value=server_ts,                                      inline=True)
    embed.add_field(name="Guild",       value=str(GUILD_ID),                                  inline=True)
    embed.add_field(name="Channel",     value="All channels", inline=True)
    embed.add_field(name="​",           value="​",                                             inline=True)  # spacer
    embed.add_field(name="Total Keys",  value=f"{total_keys:,}" if isinstance(total_keys, int) else str(total_keys),  inline=True)
    embed.add_field(name="Active Keys", value=f"{active_keys:,}" if isinstance(active_keys, int) else str(active_keys), inline=True)
    embed.add_field(name="Total Heals", value=f"{total_heals:,}" if isinstance(total_heals, int) else str(total_heals), inline=True)
    embed.set_footer(text=f"Bot: {bot.user}")
    await ctx.send(embed=embed)

# ── Error handler ─────────────────────────────────────────────────────────────

@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CheckFailure):
        if ctx.guild is None:
            return   # DMs already handled by on_message
        return  # channel restriction removed — bot listens everywhere
    if isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Member not found. Mention them with @.", delete_after=8)
        return
    LOG.error(f"Command error in {ctx.command}: {error}")

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
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
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
        embed = discord.Embed(
            title="🎫  New Support Request",
            color=C_BLUE,
            timestamp=datetime.now(timezone.utc),
        )
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
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        LOG.error(f"SupportModal error: {error}")
        await interaction.response.send_message(
            "❌ Failed to submit your request. Please try again later.",
            ephemeral=True,
        )

# ═══════════════════════════════════════════════════════════════════════════════
# ── Slash Commands ────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

# ── /status ───────────────────────────────────────────────────────────────────

@bot.tree.command(
    name="status",
    description="Check your SteamGuard license and account status",
    guild=discord.Object(id=GUILD_ID) if GUILD_ID else None,
)
async def slash_status(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        data = await _api_get(f"/admin/key-info/{interaction.user.id}")
    except Exception as e:
        LOG.error(f"/status API error: {e}")
        await interaction.followup.send("❌ Could not reach the license server. Try again later.", ephemeral=True)
        return

    if "error" in data and not data.get("keys"):
        await interaction.followup.send(
            "❌ Could not fetch your status. You may not have a key yet — use `!getkey` to get one.",
            ephemeral=True,
        )
        return

    keys = data.get("keys", [])
    if not keys:
        embed = discord.Embed(
            title="🔑  No License Found",
            description="You don't have a SteamGuard license yet. Use `!getkey` to get one.",
            color=C_GREY,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    # Use the first (most recent) key for the summary card
    k = keys[0]
    status_str = k.get("status", "unknown")
    color = {"active": C_GREEN, "paused": C_YELLOW, "revoked": C_RED}.get(status_str, C_GREY)
    status_icon = {"active": "🟢", "paused": "🟡", "revoked": "🔴"}.get(status_str, "⚪")

    embed = discord.Embed(
        title=f"{status_icon}  Your SteamGuard Status",
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
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

    embed.set_footer(text="SteamGuard • Only visible to you")
    await interaction.followup.send(embed=embed, ephemeral=True)

# ── /refer ────────────────────────────────────────────────────────────────────

@bot.tree.command(
    name="refer",
    description="Get your personal SteamGuard referral link",
    guild=discord.Object(id=GUILD_ID) if GUILD_ID else None,
)
async def slash_refer(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        data = await _api("post", "/referral/create", json={
            "discord_user_id": str(interaction.user.id),
        })
    except Exception as e:
        LOG.error(f"/refer API error: {e}")
        await interaction.followup.send("❌ Could not create referral link. Try again later.", ephemeral=True)
        return

    if "error" in data:
        await interaction.followup.send(f"❌ {data['error']}", ephemeral=True)
        return

    referral_link   = data.get("referral_link", "N/A")
    valid_referrals = data.get("valid_referrals", 0)

    embed = discord.Embed(
        title="🔗  Your SteamGuard Referral Link",
        color=C_BLUE,
    )
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
    embed.set_footer(text="SteamGuard • Only visible to you")
    await interaction.followup.send(embed=embed, ephemeral=True)

# ── /stats ────────────────────────────────────────────────────────────────────

@bot.tree.command(
    name="stats",
    description="View your SteamGuard protection statistics",
    guild=discord.Object(id=GUILD_ID) if GUILD_ID else None,
)
async def slash_stats(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)
    try:
        key_data   = await _api_get(f"/admin/key-info/{interaction.user.id}")
        badge_data = await _api_get(f"/badges/{interaction.user.id}")
    except Exception as e:
        LOG.error(f"/stats API error: {e}")
        await interaction.followup.send("❌ Could not fetch your stats. Try again later.", ephemeral=True)
        return

    if "error" in key_data and not key_data.get("keys"):
        await interaction.followup.send(
            "❌ No license found. Use `!getkey` to get started.",
            ephemeral=True,
        )
        return

    xp          = key_data.get("xp", 0)
    level       = key_data.get("level", 1)
    total_heals = key_data.get("heal_count", 0)
    total_kills = key_data.get("kill_count", 0)
    badges      = badge_data.get("badges", key_data.get("badges", []))
    last_active = key_data.get("last_heartbeat", key_data.get("last_verified", "—"))

    # Try to get streak from badge data
    streak = badge_data.get("current_streak", key_data.get("current_streak", "—"))

    embed = discord.Embed(
        title=f"📊  {interaction.user.display_name}'s SteamGuard Stats",
        color=C_BLUE,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
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
    embed.set_footer(text="Use /refer to earn rewards • SteamGuard")
    await interaction.followup.send(embed=embed)

# ── /leaderboard ──────────────────────────────────────────────────────────────

@bot.tree.command(
    name="leaderboard",
    description="View the weekly SteamGuard leaderboard",
    guild=discord.Object(id=GUILD_ID) if GUILD_ID else None,
)
async def slash_leaderboard(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)
    try:
        data = await _api_get("/leaderboard")
    except Exception as e:
        LOG.error(f"/leaderboard API error: {e}")
        await interaction.followup.send("❌ Could not fetch leaderboard. Try again later.", ephemeral=True)
        return

    if "error" in data:
        await interaction.followup.send(f"❌ {data['error']}", ephemeral=True)
        return

    entries = data.get("entries", data.get("leaderboard", []))[:10]

    embed = discord.Embed(
        title="🏆  Weekly SteamGuard Leaderboard",
        color=C_YELLOW,
        timestamp=datetime.now(timezone.utc),
    )

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

    embed.set_footer(text="Resets every Monday at midnight UTC • SteamGuard")
    await interaction.followup.send(embed=embed)

# ── /reset-device ─────────────────────────────────────────────────────────────

@bot.tree.command(
    name="reset-device",
    description="Reset your hardware binding (30-day cooldown)",
    guild=discord.Object(id=GUILD_ID) if GUILD_ID else None,
)
async def slash_reset_device(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    # Fetch the user's key first
    try:
        data = await _api_get(f"/admin/key-info/{interaction.user.id}")
    except Exception as e:
        LOG.error(f"/reset-device key fetch error: {e}")
        await interaction.followup.send("❌ Could not reach the license server. Try again later.", ephemeral=True)
        return

    keys = data.get("keys", [])
    active_keys = [k for k in keys if k.get("status") == "active"]

    if not active_keys:
        await interaction.followup.send(
            "❌ You don't have an active license key to reset.",
            ephemeral=True,
        )
        return

    license_key = active_keys[0].get("key_hash", "")

    embed = discord.Embed(
        title="⚠️  Confirm Device Reset",
        description=(
            "This will **clear your hardware binding**, allowing you to activate on a new device.\n\n"
            "**Note:** You can only reset once every **30 days**.\n\n"
            "Are you sure you want to proceed?"
        ),
        color=C_YELLOW,
    )
    embed.set_footer(text="This confirmation expires in 30 seconds")

    view = DeviceResetView(
        discord_user_id=str(interaction.user.id),
        license_key=license_key,
    )
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)

# ── /download ─────────────────────────────────────────────────────────────────

@bot.tree.command(
    name="download",
    description="Get the latest SteamGuard installer",
    guild=discord.Object(id=GUILD_ID) if GUILD_ID else None,
)
async def slash_download(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

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

    embed = discord.Embed(
        title="⬇️  SteamGuard Download",
        description=(
            f"**Version:** `{version}`\n\n"
            f"📥  **[Download SteamGuard]({UPDATE_DOWNLOAD_URL})**\n\n"
            "🔐  Always verify the **SHA-256 signature** before running any executable.\n"
            "The official hash is posted in the `#announcements` channel after each release."
        ),
        color=C_BLUE,
    )
    embed.add_field(
        name="⚠️  Safety reminder",
        value=(
            "• Only download from the official link above\n"
            "• Never run files sent to you in DMs\n"
            "• Check the hash before executing"
        ),
        inline=False,
    )
    embed.set_footer(text=f"SteamGuard v{version} • Only visible to you")
    await interaction.followup.send(embed=embed, ephemeral=True)

# ── /vote ─────────────────────────────────────────────────────────────────────

@bot.tree.command(
    name="vote",
    description="Vote on upcoming SteamGuard features",
    guild=discord.Object(id=GUILD_ID) if GUILD_ID else None,
)
async def slash_vote(interaction: discord.Interaction):
    number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

    embed = discord.Embed(
        title="🗳️  SteamGuard Feature Vote",
        description=(
            "React to this message with the number of the feature you want most!\n"
            "You can vote for multiple features.\n\n"
        ),
        color=C_BLUE,
        timestamp=datetime.now(timezone.utc),
    )

    topic_lines = []
    for i, topic in enumerate(VOTE_TOPICS[:10]):
        emoji = number_emojis[i] if i < len(number_emojis) else f"{i+1}."
        topic_lines.append(f"{emoji}  {topic}")

    embed.description += "\n".join(topic_lines)
    embed.set_footer(text="SteamGuard • Your feedback shapes the roadmap")

    await interaction.response.send_message(embed=embed, ephemeral=False)

    # Add reaction prompts to the sent message
    sent_message = await interaction.original_response()
    for i in range(min(len(VOTE_TOPICS), 10)):
        try:
            await sent_message.add_reaction(number_emojis[i])
        except Exception:
            pass

# ── /support ──────────────────────────────────────────────────────────────────

@bot.tree.command(
    name="support",
    description="Submit a support request",
    guild=discord.Object(id=GUILD_ID) if GUILD_ID else None,
)
async def slash_support(interaction: discord.Interaction):
    await interaction.response.send_modal(SupportModal())

# ── /checkbadges ──────────────────────────────────────────────────────────────

@bot.tree.command(
    name="checkbadges",
    description="Check if you earned any new badges and announce them",
    guild=discord.Object(id=GUILD_ID) if GUILD_ID else None,
)
async def slash_checkbadges(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    try:
        data = await _api_get(f"/badges/{interaction.user.id}")
    except Exception as e:
        LOG.error(f"/checkbadges API error: {e}")
        await interaction.followup.send("❌ Could not fetch badge data. Try again later.", ephemeral=True)
        return

    if "error" in data:
        await interaction.followup.send(f"❌ {data['error']}", ephemeral=True)
        return

    new_badges = data.get("new_badges", [])
    all_badges = data.get("badges", [])

    if not new_badges:
        embed = discord.Embed(
            title="🎖  Badge Check",
            description=(
                f"No new badges since last check.\n\n"
                f"You have **{len(all_badges)}** badge(s) total: "
                + (", ".join(all_badges) if all_badges else "none yet")
            ),
            color=C_GREY,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    # Announce new badges in badge channel if configured
    guild = bot.get_guild(GUILD_ID)
    if guild and BADGE_ANNOUNCE_CHANNEL_ID:
        announce_channel = guild.get_channel(BADGE_ANNOUNCE_CHANNEL_ID)
        if announce_channel:
            announce_embed = discord.Embed(
                title="🎖  New Badge Unlocked!",
                description=(
                    f"{interaction.user.mention} just earned "
                    + (", ".join(f"**{b}**" for b in new_badges))
                    + "!"
                ),
                color=C_GREEN,
                timestamp=datetime.now(timezone.utc),
            )
            announce_embed.set_thumbnail(url=interaction.user.display_avatar.url)
            announce_embed.set_footer(text="SteamGuard • Keep protecting to earn more")
            try:
                await announce_channel.send(embed=announce_embed)
            except Exception as e:
                LOG.warning(f"Could not post badge announcement: {e}")

    confirm_embed = discord.Embed(
        title="🎖  New Badge(s) Earned!",
        description=(
            "You unlocked: " + ", ".join(f"**{b}**" for b in new_badges) + "\n\n"
            "An announcement has been posted in the server. Congrats!"
        ),
        color=C_GREEN,
    )
    await interaction.followup.send(embed=confirm_embed, ephemeral=True)


# ── /rewards ──────────────────────────────────────────────────────────────────

@bot.tree.command(
    name="rewards",
    description="See all ways to earn free SteamGuard time",
)
async def cmd_rewards(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(f"{LICENSE_SERVER_URL}/rewards/status/{uid}")
            status = resp.json() if resp.status_code == 200 else {}
    except Exception:
        status = {}
    is_owner  = status.get("is_owner", False)
    total_hrs = status.get("total_reward_hours", 0.0)
    triggers  = status.get("triggers", {})
    embed = discord.Embed(
        title="\u23f0  SteamGuard Rewards",
        description=("Earn free hours by completing these actions."
                     if not is_owner else
                     "\u267e\ufe0f  You have unlimited access as an owner."),
        color=0x23A559,
    )
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
    embed.set_footer(text="Use /invite-friends to share your referral link")
    await interaction.followup.send(embed=embed, ephemeral=True)


# ── /invite-friends ───────────────────────────────────────────────────────────

@bot.tree.command(
    name="invite-friends",
    description="Get your referral link — earn +3h for each friend who activates",
)
async def cmd_invite_friends(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    if interaction.user.id in OWNER_DISCORD_IDS:
        await interaction.followup.send(
            "\u267e\ufe0f Owner account \u2014 you have unlimited access.", ephemeral=True)
        return
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(f"{LICENSE_SERVER_URL}/referral/create",
                json={"discord_user_id": uid, "admin_key": ADMIN_KEY})
        data = resp.json()
    except Exception as e:
        await interaction.followup.send(f"\u274c Could not create referral link: {e}", ephemeral=True)
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
    embed = discord.Embed(
        title="\U0001f517  Invite Friends \u2014 Earn +3h Each",
        description=(
            f"For every friend who joins **and activates SteamGuard**, "
            f"you earn **+3 free hours** of access.\n\n"
            f"Share your link or copy the pre-written message below."
        ),
        color=0x5865F2,
    )
    embed.add_field(name="\U0001f517  Your Referral Link",  value=f"`{referral_link}`",   inline=False)
    embed.add_field(name="\u2705  Valid Referrals",         value=str(valid_referrals),   inline=True)
    embed.add_field(name="\u23f3  Pending",                 value=str(pending),           inline=True)
    embed.add_field(name="\u23f0  Total Earned",            value=f"{earned_hours:.0f}h", inline=True)
    embed.add_field(name="\U0001f4cb  Copy this message to DM friends:",
                    value=f"```\n{invite_msg}\n```", inline=False)
    embed.set_footer(text="Credits apply after your friend activates their key")
    await interaction.followup.send(embed=embed, ephemeral=True)


# ── /daily ────────────────────────────────────────────────────────────────────

@bot.tree.command(name="daily", description="Claim your daily +30 min reward (once per ~20h)")
async def cmd_daily(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    uid = str(interaction.user.id)
    if interaction.user.id in OWNER_DISCORD_IDS:
        await interaction.followup.send("\u267e\ufe0f Owner \u2014 unlimited access.", ephemeral=True)
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
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)
        return
    if result.get("granted"):
        embed = discord.Embed(title="\u2705  Daily Reward Claimed!",
                              description="\U0001f552 **+30 minutes** added to your SteamGuard access.",
                              color=0x23A559)
        embed.set_footer(text="Come back tomorrow for another reward")
    else:
        embed = discord.Embed(title="\u23f3  Already Claimed",
                              description=result.get("reason", "Try again later."),
                              color=0xF0B232)
    await interaction.followup.send(embed=embed, ephemeral=True)


# ── /grant-reward (admin) ─────────────────────────────────────────────────────

@bot.tree.command(name="grant-reward", description="[Admin] Manually grant a reward to a user")
@discord.app_commands.describe(user="Discord user to reward",
                                trigger="Reward trigger (e.g. bug_report)",
                                override="Bypass cooldown/cap?")
async def cmd_grant_reward(interaction: discord.Interaction,
                            user: discord.Member, trigger: str, override: bool = False):
    if interaction.user.id not in ADMIN_USER_IDS:
        await interaction.response.send_message("\u274c Admin only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(f"{LICENSE_SERVER_URL}/rewards/grant",
                json={"discord_user_id": str(user.id), "trigger": trigger, "admin_override": override},
                headers={"x-admin-key": ADMIN_KEY})
        result = resp.json()
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)
        return
    if result.get("granted"):
        embed = discord.Embed(
            title="\U0001f381  Reward Granted",
            description=(f"Gave **{user.display_name}** {result['reason']}\n"
                         f"Total hours now: **{result.get('new_total_hours', 0):.1f}h**"),
            color=0x23A559)
        try:
            await user.send(f"\U0001f381 You received a SteamGuard reward from an admin!\n"
                            f"{result['reason']}\nYour total: {result.get('new_total_hours', 0):.1f}h")
        except Exception:
            pass
    else:
        embed = discord.Embed(title="\u274c Not Granted",
                              description=result.get("reason", "Unknown"), color=0xF23F43)
    await interaction.followup.send(embed=embed, ephemeral=True)


# ── Slash command error handler ───────────────────────────────────────────────

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    LOG.error(f"Slash command error: {error}")
    msg = "❌ An unexpected error occurred. Please try again later."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass

# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
