"""
SteamGuard Discord Bot
----------------------
Commands:
  !getkey          — DMs the user their license key (creates one if needed)
  !revokekey @user — Admin: revoke a user's key
  !status          — Check your own key status

Auto-tasks:
  Every 24 h: sweeps all members, revokes keys for anyone who lost the role.

Setup:
  1. Create a Discord application at https://discord.com/developers/applications
  2. Add a Bot, enable Server Members Intent + Message Content Intent
  3. Invite bot with scope: bot + applications.commands
     Permissions: Read Messages, Send Messages, Manage Roles, Ban Members
  4. Set env vars:
       DISCORD_BOT_TOKEN   — bot token
       DISCORD_GUILD_ID    — your server ID
       DISCORD_ROLE_ID     — "Member" role ID that grants access
       LICENSE_SERVER_URL  — https://your-cloud-run-url
       ADMIN_KEY           — same as server ADMIN_KEY secret
"""

import os
import asyncio
import logging
import httpx
import discord
from discord.ext import commands, tasks

LOG = logging.getLogger("sg-bot")
logging.basicConfig(level=logging.INFO)

GUILD_ID            = int(os.environ["DISCORD_GUILD_ID"])
ROLE_ID             = int(os.environ["DISCORD_ROLE_ID"])
LICENSE_SERVER_URL  = os.environ["LICENSE_SERVER_URL"]
ADMIN_KEY           = os.environ["ADMIN_KEY"]
BOT_TOKEN           = os.environ["DISCORD_BOT_TOKEN"]

# Discord user IDs who can run admin commands (add yours)
ADMIN_USER_IDS: set[int] = set(
    int(x) for x in os.environ.get("ADMIN_USER_IDS", "").split(",") if x.strip()
)

# ── Bot setup ─────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.members         = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ── Server API helpers ────────────────────────────────────────────────────────

async def _api(method: str, path: str, **kwargs) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await getattr(client, method)(
            f"{LICENSE_SERVER_URL}{path}",
            headers={"x-admin-key": ADMIN_KEY},
            **kwargs)
        return r.json()

async def generate_key(discord_user_id: str, note: str = "") -> str | None:
    try:
        data = await _api("post", "/generate", json={
            "discord_user_id": discord_user_id,
            "note": note,
        })
        return data.get("key")
    except Exception as e:
        LOG.error(f"generate_key error: {e}")
        return None

async def revoke_key_for_user(discord_user_id: str, reason: str = ""):
    """Revoke all keys belonging to a Discord user ID."""
    # The server stores discord_user_id per license.
    # We call the /revoke-by-discord endpoint.
    try:
        await _api("post", "/revoke-by-discord", json={
            "discord_user_id": discord_user_id,
            "reason": reason,
        })
    except Exception as e:
        LOG.error(f"revoke error for {discord_user_id}: {e}")

# ── Events ────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    LOG.info(f"Bot ready as {bot.user} (ID {bot.user.id})")
    daily_membership_sweep.start()

@bot.event
async def on_member_remove(member: discord.Member):
    """User left the server — revoke immediately."""
    if member.guild.id != GUILD_ID:
        return
    LOG.info(f"{member} left — revoking key")
    await revoke_key_for_user(str(member.id), reason="Left Discord server")

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """User lost the required role — revoke."""
    if after.guild.id != GUILD_ID:
        return
    role = after.guild.get_role(ROLE_ID)
    if role is None:
        return
    had_role = role in before.roles
    has_role = role in after.roles
    if had_role and not has_role:
        LOG.info(f"{after} lost Member role — revoking key")
        await revoke_key_for_user(str(after.id), reason="Lost Member role")

# ── Commands ──────────────────────────────────────────────────────────────────

@bot.command(name="getkey")
async def get_key(ctx: commands.Context):
    """DM the user their license key."""
    if ctx.guild is None or ctx.guild.id != GUILD_ID:
        return

    role = ctx.guild.get_role(ROLE_ID)
    if role not in ctx.author.roles:
        await ctx.send(
            f"❌ {ctx.author.mention} You need the **Member** role to get a key.\n"
            "Subscribe / join properly first, then run `!getkey` again.",
            delete_after=15)
        return

    await ctx.message.delete()   # don't expose the command in public channel

    key = await generate_key(str(ctx.author.id), note=ctx.author.name)
    if not key:
        await ctx.author.send("❌ Could not generate a key right now. Try again in a minute.")
        return

    embed = discord.Embed(
        title="🔑  Your SteamGuard License Key",
        color=0x2563eb)
    embed.add_field(name="Key", value=f"```{key}```", inline=False)
    embed.add_field(
        name="⚠️  Keep this private",
        value="Do NOT share your key. It is locked to your machine and your Discord account.",
        inline=False)
    embed.add_field(
        name="📋  How to activate",
        value="1. Launch **SteamGuard.exe**\n"
              "2. Accept the Terms of Service\n"
              "3. Enter your key and click **Activate**\n"
              "4. The app will verify your Discord membership automatically",
        inline=False)
    embed.set_footer(text="SteamGuard • Key valid while you remain a Member")

    try:
        await ctx.author.send(embed=embed)
        await ctx.send(f"✅ {ctx.author.mention} Key sent to your DMs!", delete_after=8)
    except discord.Forbidden:
        await ctx.send(
            f"❌ {ctx.author.mention} I can't DM you. "
            "Enable **Allow direct messages from server members** in your Privacy Settings.",
            delete_after=20)

@bot.command(name="revokekey")
@commands.has_permissions(administrator=True)
async def revoke_key_cmd(ctx: commands.Context, member: discord.Member):
    """Admin: revoke a member's key immediately."""
    if ctx.author.id not in ADMIN_USER_IDS and not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Not authorized.", delete_after=5)
        return
    await revoke_key_for_user(str(member.id), reason=f"Manual revoke by {ctx.author}")
    await ctx.send(f"✅ Key for {member.mention} revoked.")

@bot.command(name="sgstatus")
async def sg_status(ctx: commands.Context):
    """Check bot health."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{LICENSE_SERVER_URL}/health")
        server_ok = r.status_code == 200
    except Exception:
        server_ok = False

    embed = discord.Embed(
        title="SteamGuard Status",
        color=0x22c55e if server_ok else 0xef4444)
    embed.add_field(name="Bot",    value="✅ Online",                          inline=True)
    embed.add_field(name="Server", value="✅ Online" if server_ok else "❌ Down", inline=True)
    await ctx.send(embed=embed)

# ── Daily membership sweep ────────────────────────────────────────────────────

@tasks.loop(hours=24)
async def daily_membership_sweep():
    """
    Every 24 h: fetch all members with the role,
    compare against DB (via /admin/active-discord-ids),
    revoke anyone who no longer qualifies.
    """
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        LOG.warning("Guild not found in sweep")
        return

    role = guild.get_role(ROLE_ID)
    if role is None:
        LOG.warning("Role not found in sweep")
        return

    members_with_role = {str(m.id) for m in role.members}
    LOG.info(f"Sweep: {len(members_with_role)} members with role")

    # Get all active Discord IDs from the license server
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{LICENSE_SERVER_URL}/admin/active-discord-ids",
                headers={"x-admin-key": ADMIN_KEY})
        if r.status_code != 200:
            LOG.warning(f"Sweep: could not fetch active IDs ({r.status_code})")
            return
        active_ids: list[str] = r.json().get("ids", [])
    except Exception as e:
        LOG.error(f"Sweep fetch error: {e}")
        return

    revoked = 0
    for discord_id in active_ids:
        if discord_id not in members_with_role:
            LOG.info(f"Sweep: {discord_id} lost role — revoking")
            await revoke_key_for_user(discord_id, reason="Daily sweep: no longer Member")
            revoked += 1

    LOG.info(f"Sweep complete: {revoked} keys revoked")

@daily_membership_sweep.before_loop
async def before_sweep():
    await bot.wait_until_ready()

# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
