import os
import discord
from discord.ext import commands
from google.cloud import secretmanager

# Get Discord bot token from Secret Manager
def get_bot_token():
    project_id = os.environ.get("GCP_PROJECT", "fabled-mystery-474200-i1")
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/discord-bot-token/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"SteamGuard bot logged in as {bot.user}")

@bot.command()
async def ping(ctx):
    await ctx.send("Pong! SteamGuard is online.")

@bot.command()
async def status(ctx):
    await ctx.send("SteamGuard service is running on Google Cloud.")

if __name__ == "__main__":
    token = get_bot_token()
    bot.run(token)
