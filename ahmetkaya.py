# ahmetkaya.py
import os
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
from dotenv import load_dotenv

#  .env dosyasından tokeni yükle
load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix='c', intents=intents)

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': False,
    'cookiefile': 'youtube.txt',
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'extract_flat': False,
    'source_address': '0.0.0.0',
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)
queue = asyncio.Queue()

# ------------------- YARDIMCI -------------------
async def connect_to_voice(ctx):
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("Ses kanalında olman gerekiyor.")
        return False

    if ctx.voice_client:
        return True

    try:
        await ctx.author.voice.channel.connect()
        return True
    except Exception as e:
        await ctx.send(f"Bağlanılamadı: `{e}`")
        return False


async def play_next_from_queue(ctx):
    if queue.empty():
        # Kuyruk boşsa, bağlantıyı kesmek istersen aşağıdaki satırı aktif et:
        # if ctx.voice_client: await ctx.voice_client.disconnect() 
        return False

    title, source, requester = await queue.get()
    ctx.voice_client.play(
        source,
        after=lambda e: asyncio.run_coroutine_threadsafe(play_next_from_queue(ctx), bot.loop)
    )
    embed = discord.Embed(
        title="Şimdi Çalıyor",
        description=f"**{title}**",
        color=discord.Color.green()
    ).set_footer(text=f"İsteyen: {requester}")
    await ctx.send(embed=embed)
    return True


async def play_next_radio(ctx):
    """YouTube Radio: Çalan şarkının devamını çeker"""
    if not ctx.voice_client or not ctx.voice_client.source:
        await ctx.send("Radio için çalan şarkı yok.")
        return

    # YTDLSource'tan URL al
    current_source = ctx.voice_client.source.original if hasattr(ctx.voice_client.source, 'original') else ctx.voice_client.source
    current_url = getattr(current_source, 'url', None)
    if not current_url:
        await ctx.send("Şarkı URL'si alınamadı.")
        return

    try:
        info = await asyncio.get_event_loop().run_in_executor(
            None, lambda: ytdl.extract_info(f"{current_url}&start_radio=1", download=False)
        )
        if 'entries' in info and len(info['entries']) > 1:
            next_entry = info['entries'][1]
            if next_entry:
                filename = next_entry['url']
                source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(filename, **FFMPEG_OPTIONS), volume=0.7)
                
                # URL'yi kaydetmek için YTDLSource sınıfını kullanmıyorsak geçici bir nesne oluştur
                # Bu kısım kodun orijinalinde karmaşıktı, YTDLSource'u doğru kullanmalıyız.
                # Basitlik için sadece temel bilgileri saklayalım.

                ctx.voice_client.play(
                    source,
                    after=lambda e: asyncio.run_coroutine_threadsafe(play_next_from_queue(ctx), bot.loop)
                )
                embed = discord.Embed(
                    title="Radio: Yeni Şarkı",
                    description=f"**{next_entry['title']}**",
                    color=discord.Color.purple()
                ).set_footer(text="YouTube Radio")
                await ctx.send(embed=embed)
                return
    except Exception as e:
        await ctx.send(f"Radio hatası: `{e}`")

    await ctx.send("Yeni şarkı önerisi alınamadı.")


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, requester, volume=0.7):
        super().__init__(source, volume)
        self.original = source
        self.data = data
        self.title = data.get('title')
        self.url = data.get('webpage_url')
        self.requester = str(requester)

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True, requester=None):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(
            None, lambda: ytdl.extract_info(url, download=not stream)
        )

        if 'entries' in data:
            entries = [e for e in data['entries'] if e]
            sources = []
            for entry in entries:
                filename = entry['url'] if stream else ytdl.prepare_filename(entry)
                ffmpeg_source = discord.FFmpegPCMAudio(filename, **FFMPEG_OPTIONS)
                source = cls(ffmpeg_source, data=entry, requester=requester)
                sources.append((entry['title'], source, str(requester)))
            return sources
        else:
            filename = data['url'] if stream else ytdl.prepare_filename(data)
            ffmpeg_source = discord.FFmpegPCMAudio(filename, **FFMPEG_OPTIONS)
            return [(data['title'], cls(ffmpeg_source, data=data, requester=requester), str(requester))]


async def enqueue_and_play(ctx, sources):
    if not ctx.voice_client:
        if not await connect_to_voice(ctx):
            return

    added = 0
    for title, source, requester in sources:
        await queue.put((title, source, requester))
        added += 1

    if added > 1:
        await ctx.send(f"Playlist eklendi: **{added} şarkı** kuyruğa alındı.")
    else:
        await ctx.send(f"Kuyruğa eklendi: **{sources[0][0]}**")

    if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
        await play_next_from_queue(ctx)


async def local_source(file_path):
    return os.path.basename(file_path), discord.FFmpegPCMAudio(file_path, **FFMPEG_OPTIONS)


# ------------------- KOMUTLAR -------------------
@bot.command(aliases=['p'])
async def cplay(ctx, *, query: str):
    if not await connect_to_voice(ctx):
        return

    full_path = os.path.join(os.getcwd(), query)
    if os.path.isfile(full_path):
        title, source = await local_source(full_path)
        source = discord.PCMVolumeTransformer(source, volume=0.7)
        await enqueue_and_play(ctx, [(title, source, ctx.author.display_name)])
        return

    try:
        sources = await YTDLSource.from_url(query, loop=bot.loop, stream=True, requester=ctx.author)
        await enqueue_and_play(ctx, sources)
    except Exception as e:
        await ctx.send(f"Hata: `{e}`")


async def skip_song(ctx):
    if not ctx.voice_client:
        await ctx.send("Ses kanalında değilim.")
        return

    if ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        if not await play_next_from_queue(ctx):
            await ctx.send("Kuyruk boş.")
    else:
        if not await play_next_from_queue(ctx):
            await ctx.send("Kuyruk boş.")


async def next_radio(ctx):
    if not ctx.voice_client:
        await ctx.send("Ses kanalında değilim.")
        return

    if ctx.voice_client.is_playing():
        ctx.voice_client.stop()

    await play_next_radio(ctx)


@bot.command(aliases=['s'])
async def cskip(ctx):
    await skip_song(ctx)


@bot.command(aliases=['n'])
async def cnext(ctx):
    await next_radio(ctx)


@bot.command()
async def cpause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("Duraklatıldı.")
    else:
        await ctx.send("Çalan bir şey yok.")


@bot.command()
async def cresume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("Devam ediliyor.")
    else:
        await ctx.send("Duraklatılmış bir şey yok.")


@bot.command(aliases=['stop', 'kick'])
async def cstop(ctx):
    if ctx.voice_client:
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
            ctx.voice_client.stop()

        try:
            await ctx.voice_client.disconnect()
            await ctx.send("Bot kanaldan ayrıldı ve kuyruk temizlendi.")
        except Exception as e:
            await ctx.send(f"Ayrılma hatası: `{e}`")
    else:
        await ctx.send("Ses kanalında değilim.")


@bot.command()
async def cqueue(ctx):
    if queue.empty():
        await ctx.send("Kuyruk boş.")
        return
    items = list(queue._queue)
    msg = "**Kuyruk:**\n"
    for i, (title, _, requester) in enumerate(items[:10], 1):
        msg += f"{i}. **{title}** - {requester}\n"
    if len(items) > 10:
        msg += f"... ve {len(items)-10} daha."
    await ctx.send(msg)


@bot.command()
async def chelp(ctx):
    embed = discord.Embed(title="Ahmet Kaya Bot", color=discord.Color.blue())
    embed.add_field(name="Komutlar", value=(
        "`cplay <link>` → Çal\n"
        "`cskip` → Kuyruktan sonraki\n"
        "`cnext` → Yeni YouTube önerisi (radio)\n"
        "`cpause` → Dur\n"
        "`cresume` → Devam\n"
        "`cstop` → Botu at\n"
        "`cqueue` → Kuyruk"
    ), inline=False)
    await ctx.send(embed=embed)


# ------------------- SLASH KOMUTLARI (GLOBAL) -------------------
# GUILD_ID kaldırıldı, global komutlar için bu satır gereksiz.
# GUILD_ID = discord.Object(id=431478809745948672)

async def safe_send(interaction, content=None, *, embed=None):
    await interaction.followup.send(content=content, embed=embed)

@bot.tree.command(name="play", description="Çal") # guild=GUILD_ID kaldırıldı
@app_commands.describe(input="Link, şarkı adı")
async def slash_play(interaction: discord.Interaction, input: str):
    await interaction.response.defer()
    ctx = await bot.get_context(interaction)
    ctx.author = interaction.user
    ctx.send = lambda content=None, *, embed=None: safe_send(interaction, content, embed=embed)
    await cplay(ctx, query=input)


@bot.tree.command(name="skip", description="Kuyruktan sonraki şarkı") # guild=GUILD_ID kaldırıldı
async def slash_skip(interaction: discord.Interaction):
    await interaction.response.defer()
    ctx = await bot.get_context(interaction)
    ctx.send = lambda content=None, *, embed=None: safe_send(interaction, content, embed=embed)
    await skip_song(ctx)


@bot.tree.command(name="next", description="Yeni YouTube önerisi (radio)") # guild=GUILD_ID kaldırıldı
async def slash_next(interaction: discord.Interaction):
    await interaction.response.defer()
    ctx = await bot.get_context(interaction)
    ctx.send = lambda content=None, *, embed=None: safe_send(interaction, content, embed=embed)
    await next_radio(ctx)


@bot.tree.command(name="pause", description="Duraklat") # guild=GUILD_ID kaldırıldı
async def slash_pause(interaction: discord.Interaction):
    await interaction.response.defer()
    ctx = await bot.get_context(interaction)
    ctx.send = lambda content=None, *, embed=None: safe_send(interaction, content, embed=embed)
    await cpause(ctx)


@bot.tree.command(name="resume", description="Devam et") # guild=GUILD_ID kaldırıldı
async def slash_resume(interaction: discord.Interaction):
    await interaction.response.defer()
    ctx = await bot.get_context(interaction)
    ctx.send = lambda content=None, *, embed=None: safe_send(interaction, content, embed=embed)
    await cresume(ctx)


@bot.tree.command(name="stop", description="Botu kanaldan at") # guild=GUILD_ID kaldırıldı
async def slash_stop(interaction: discord.Interaction):
    await interaction.response.defer()
    ctx = await bot.get_context(interaction)
    ctx.send = lambda content=None, *, embed=None: safe_send(interaction, content, embed=embed)
    await cstop(ctx)


@bot.tree.command(name="queue", description="Kuyruğu göster") # guild=GUILD_ID kaldırıldı
async def slash_queue(interaction: discord.Interaction):
    await interaction.response.defer()
    ctx = await bot.get_context(interaction)
    ctx.send = lambda content=None, *, embed=None: safe_send(interaction, content, embed=embed)
    await cqueue(ctx)


@bot.tree.command(name="help", description="Yardım") # guild=GUILD_ID kaldırıldı
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(title="Ahmet Kaya Bot", color=discord.Color.gold())
    embed.add_field(name="Komutlar", value=(
        "/play → Çal\n"
        "/skip → Kuyruktan sonraki\n"
        "/next → Yeni YouTube önerisi\n"
        "/pause → Dur\n"
        "/resume → Devam\n"
        "/stop → At\n"
        "/queue → Kuyruk"
    ), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ------------------- BOT HAZIR -------------------
@bot.event
async def on_ready():
    try:
        # Guild ID belirtilmedi, global senkronizasyon yapıldı
        await bot.tree.sync() 
        print(f"{bot.user} hazır! Global komutlar senkronize edildi. (/next = Radio!)")
    except Exception as e:
        print(f"Slash komutlar senkronize edilemedi: {e}")

# KOD ÇALIŞTIRMA
if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_TOKEN"))