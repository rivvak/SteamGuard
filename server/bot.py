"""
SteamGuard Discord Bot — Full Version
──────────────────────────────────────
Channel rules:
  • All commands ONLY work in #get-key channel (GETKEY_CHANNEL_ID env var)
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
  !sgstatus                              — bot + server health check

Auto-tasks:
  Every 24 h: membership sweep — pauses keys for anyone who lost the role

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
"""

import os
import asyncio
import logging
import time
import httpx
import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone

LOG = logging.getLogger("sg-bot")
logging.basicConfig(level=logging.INFO)

GUILD_ID           = int(os.environ["DISCORD_GUILD_ID"])
ROLE_ID            = int(os.environ["DISCORD_ROLE_ID"])
GETKEY_CHANNEL_ID  = int(os.environ.get("GETKEY_CHANNEL_ID", "0"))
LICENSE_SERVER_URL = os.environ["LICENSE_SERVER_URL"]
ADMIN_KEY          = os.environ["ADMIN_KEY"]
BOT_TOKEN          = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_INVITE     = os.environ.get("DISCORD_INVITE", "https://discord.gg/REPLACE")

ADMIN_USER_IDS: set[int] = set(
    int(x) for x in os.environ.get("ADMIN_USER_IDS", "").split(",") if x.strip()
)

YT_CLIENT_ID     = os.environ.get("YOUTUBE_CLIENT_ID", "")
YT_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET", "")

# Colours
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

# ── Channel guard decorator ───────────────────────────────────────────────────

def in_getkey_channel():
    """Only allow command if used in #get-key channel (or GETKEY_CHANNEL_ID = 0 = any)."""
    async def predicate(ctx: commands.Context):
        if GETKEY_CHANNEL_ID == 0:
            return True
        if ctx.guild is None:
            return False   # DMs handled separately
        return ctx.channel.id == GETKEY_CHANNEL_ID
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

    # Block commands in wrong channel (non-admin users)
    if (GETKEY_CHANNEL_ID != 0
            and message.content.startswith("!")
            and message.channel.id != GETKEY_CHANNEL_ID
            and message.author.id not in ADMIN_USER_IDS
            and not message.author.guild_permissions.administrator):
        # Only tell them if it starts with a known command
        known = {"!getkey", "!mykey", "!linkyoutube"}
        cmd   = message.content.split()[0].lower()
        if cmd in known:
            channel = message.guild.get_channel(GETKEY_CHANNEL_ID)
            ref     = channel.mention if channel else "#get-key"
            await message.reply(
                f"❌ That command only works in {ref}.",
                delete_after=8)
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
    if role in before.roles and role not in after.roles:
        LOG.info(f"{after} lost Member role — pausing key")
        await _api("post", "/pause-by-discord", json={
            "discord_user_id": str(after.id),
            "reason": "Lost Member role",
        })

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

    # Try to send (don't error if we can't)
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
    """Admin: bot + server health."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{LICENSE_SERVER_URL}/health")
        server_ok = r.status_code == 200
        server_ts = r.json().get("ts", "?") if server_ok else "—"
    except Exception:
        server_ok = False
        server_ts = "—"

    embed = discord.Embed(
        title="SteamGuard System Status",
        color=C_GREEN if server_ok else C_RED)
    embed.add_field(name="Bot",    value="🟢 Online",                                  inline=True)
    embed.add_field(name="Server", value=f"{'🟢 Online' if server_ok else '🔴 Down'}",  inline=True)
    embed.add_field(name="Server time", value=server_ts, inline=True)
    embed.add_field(name="Guild",  value=str(GUILD_ID),  inline=True)
    embed.add_field(name="Channel", value=f"<#{GETKEY_CHANNEL_ID}>" if GETKEY_CHANNEL_ID else "any", inline=True)
    embed.set_footer(text=f"Bot: {bot.user}")
    await ctx.send(embed=embed)

# ── Error handler ─────────────────────────────────────────────────────────────

@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CheckFailure):
        if ctx.guild is None:
            return   # DMs already handled by on_message
        if GETKEY_CHANNEL_ID != 0 and ctx.channel.id != GETKEY_CHANNEL_ID:
            channel = ctx.guild.get_channel(GETKEY_CHANNEL_ID)
            ref     = channel.mention if channel else "#get-key"
            await ctx.send(f"❌ Use that command in {ref}.", delete_after=6)
        return
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

# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
