"""/memory slash command group (Phase 4).

Lets a Discord user inspect and manage the memory the AI has of their prior
/create + /develop invocations. Memory is stored per-user (all channels) and
per-channel (all users in that channel) in GCS
(gs://steamguard-create-artifacts/memory/), and is automatically injected as
PRIOR_CONTEXT.md into every /create sandbox and every /develop prompt.

Subcommands:

    /memory show   scope:<user|channel>          → DMs you a paginated dump
    /memory clear  scope:<user|channel|all>     → confirm-then-delete
    /memory forget id:<entry_id>                → surgical single-entry delete
                                                  (looks up in your user scope first,
                                                   then the current channel scope)

Owner override: `/memory show user:<discord_id>` lets the owner peek at another
user's memory (used for debugging poisoned context).
"""

from __future__ import annotations

import os
from typing import Optional

import discord
import httpx
from discord import app_commands
from discord.ext import commands

LICENSE_SERVER_URL = os.environ["LICENSE_SERVER_URL"].rstrip("/")
ADMIN_KEY = os.environ["ADMIN_KEY"]
GUILD_ID = int(os.environ["DISCORD_GUILD_ID"])
OWNER_ID = int(os.environ.get("DISCORD_OWNER_ID", "1513150836472021074"))
GUILD = discord.Object(id=GUILD_ID)


class _ConfirmClear(discord.ui.View):
    def __init__(self, on_confirm, on_cancel):
        super().__init__(timeout=30)
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
        self.decided = False

    @discord.ui.button(label="Yes, wipe it", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        if self.decided:
            return
        self.decided = True
        await self.on_confirm(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        if self.decided:
            return
        self.decided = True
        await self.on_cancel(interaction)


class MemoryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._http = httpx.AsyncClient(timeout=60.0)

    group = app_commands.Group(
        name="memory",
        description="Manage the AI's memory of your prior /create and /develop commands.",
        guild_ids=[GUILD_ID],
    )

    @group.command(
        name="show",
        description="Show the AI's memory for you (or a channel).",
    )
    @app_commands.describe(
        scope="Which memory scope to inspect",
        user_id="(Owner only) Look up a different user's memory by discord id",
    )
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="my personal memory (all channels)", value="user"),
            app_commands.Choice(name="this channel's memory (everyone)", value="channel"),
        ]
    )
    async def show(
        self,
        interaction: discord.Interaction,
        scope: app_commands.Choice[str],
        user_id: Optional[str] = None,
    ):
        scope_val = scope.value
        target_id = self._resolve_scope_id(interaction, scope_val, user_id)
        if target_id is None:
            return  # error already sent

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            r = await self._http.get(
                f"{LICENSE_SERVER_URL}/internal/memory/show/{scope_val}/{target_id}",
                headers={"X-Admin-Key": ADMIN_KEY},
                params={"discord_user_id": str(interaction.user.id)},
            )
        except httpx.HTTPError as e:
            await interaction.followup.send(f"Network error: {e}", ephemeral=True)
            return

        if r.status_code == 403:
            await interaction.followup.send(
                "You can only view your own user memory. (Owner override exists.)",
                ephemeral=True,
            )
            return
        if r.status_code != 200:
            await interaction.followup.send(
                f"Server error {r.status_code}: {r.text[:300]}", ephemeral=True
            )
            return

        data = r.json()
        md = data.get("markdown", "")
        if not md.strip():
            md = f"_(no memory in {scope_val}:{target_id})_"

        # Discord message cap 2000 chars — chunk if longer
        for i in range(0, len(md), 1900):
            chunk = md[i : i + 1900]
            await interaction.followup.send(chunk, ephemeral=True)

    @group.command(
        name="clear",
        description="Wipe a memory scope (with confirmation).",
    )
    @app_commands.describe(scope="Which memory scope to wipe")
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="my personal memory (all channels)", value="user"),
            app_commands.Choice(name="this channel's memory (everyone)", value="channel"),
            app_commands.Choice(name="BOTH (personal + this channel)", value="all"),
        ]
    )
    async def clear(
        self,
        interaction: discord.Interaction,
        scope: app_commands.Choice[str],
    ):
        scope_val = scope.value

        async def _do_clear(btn_interaction: discord.Interaction):
            await btn_interaction.response.edit_message(
                content="Clearing…", view=None
            )
            results = []
            targets: list[tuple[str, str]] = []
            if scope_val in ("user", "all"):
                targets.append(("user", str(interaction.user.id)))
            if scope_val in ("channel", "all"):
                if interaction.channel_id:
                    targets.append(("channel", str(interaction.channel_id)))
            for sc, sid in targets:
                try:
                    r = await self._http.post(
                        f"{LICENSE_SERVER_URL}/internal/memory/clear/{sc}/{sid}",
                        headers={"X-Admin-Key": ADMIN_KEY},
                        params={"discord_user_id": str(interaction.user.id)},
                    )
                    ok = r.status_code == 200 and r.json().get("ok")
                    results.append(f"• {sc}:`{sid}` — {'✅ cleared' if ok else f'❌ HTTP {r.status_code}'}")
                except httpx.HTTPError as e:
                    results.append(f"• {sc}:`{sid}` — network error: {e}")
            await btn_interaction.edit_original_response(
                content="**Memory clear results:**\n" + "\n".join(results),
                view=None,
            )

        async def _do_cancel(btn_interaction: discord.Interaction):
            await btn_interaction.response.edit_message(
                content="Cancelled — memory left intact.", view=None
            )

        view = _ConfirmClear(_do_clear, _do_cancel)
        scope_desc = {
            "user": "your personal memory (across ALL channels)",
            "channel": "this channel's memory (everyone who used /create or /develop here)",
            "all": "BOTH your personal memory AND this channel's memory",
        }[scope_val]
        await interaction.response.send_message(
            f"⚠️ Wipe {scope_desc}? This can't be undone.",
            view=view,
            ephemeral=True,
        )

    @group.command(
        name="forget",
        description="Delete one specific entry by id (get ids from /memory show).",
    )
    @app_commands.describe(entry_id="The 12-char id shown in /memory show output")
    async def forget(self, interaction: discord.Interaction, entry_id: str):
        entry_id = entry_id.strip()
        if not (6 <= len(entry_id) <= 32):
            await interaction.response.send_message(
                "entry_id looks wrong (should be ~12 chars). Get one from `/memory show`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        removed_from: list[str] = []
        errors: list[str] = []
        for scope, sid in (
            ("user", str(interaction.user.id)),
            ("channel", str(interaction.channel_id or "0")),
        ):
            try:
                r = await self._http.post(
                    f"{LICENSE_SERVER_URL}/internal/memory/forget/{scope}/{sid}",
                    headers={
                        "X-Admin-Key": ADMIN_KEY,
                        "Content-Type": "application/json",
                    },
                    params={"discord_user_id": str(interaction.user.id)},
                    json={"entry_id": entry_id},
                )
                if r.status_code == 200:
                    removed_from.append(f"{scope}:`{sid}`")
                elif r.status_code == 404:
                    pass  # not present in this scope, fine
                else:
                    errors.append(f"{scope}:`{sid}` → HTTP {r.status_code}")
            except httpx.HTTPError as e:
                errors.append(f"{scope}:`{sid}` → {type(e).__name__}: {e}")

        if removed_from:
            msg = f"✅ Removed `{entry_id}` from: " + ", ".join(removed_from)
        elif errors:
            msg = "❌ Errors:\n" + "\n".join(errors)
        else:
            msg = f"No entry `{entry_id}` found in your memory or this channel's memory."

        await interaction.followup.send(msg, ephemeral=True)

    # ── helpers ──

    def _resolve_scope_id(
        self,
        interaction: discord.Interaction,
        scope: str,
        user_id_override: Optional[str],
    ) -> Optional[str]:
        if scope == "channel":
            if not interaction.channel_id:
                # DM or ephemeral context — no channel scope
                self._must_send(
                    interaction,
                    "No channel context on this interaction — try inside a guild channel.",
                )
                return None
            return str(interaction.channel_id)

        # scope == 'user'
        if user_id_override:
            if interaction.user.id != OWNER_ID:
                self._must_send(
                    interaction,
                    "Only the owner may look up another user's memory.",
                )
                return None
            return user_id_override.strip()
        return str(interaction.user.id)

    def _must_send(self, interaction: discord.Interaction, msg: str) -> None:
        # Wrap this in an ensure_future so the caller stays sync
        import asyncio

        async def _send():
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)

        asyncio.ensure_future(_send())

    async def cog_unload(self) -> None:
        await self._http.aclose()


async def setup(bot: commands.Bot) -> None:
    if bot.get_cog("MemoryCog"):
        return
    await bot.add_cog(MemoryCog(bot), guilds=[GUILD])
