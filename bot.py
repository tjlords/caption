import os, re, asyncio
from flask import Flask
from pyrogram import Client, filters
from pyrogram.errors import FloodWait

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))

bot = Client("cleaner_stable", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
flask_app = Flask(__name__)

DEST = {"chat_id": None}
SETTINGS = {"find": "@ProfessorSarkari", "replace": ""}
STATE = {"running": False, "stop": False}
RANGE_RE = re.compile(r"(\d+)-(\d+)")


@flask_app.route("/")
def home(): return "🟢 Cleaner bot running"
@flask_app.route("/health")
def health(): return "✅ OK"


def clean_caption(text):
    if not text: return text
    find = SETTINGS["find"]
    rep = SETTINGS["replace"]
    if not find: return text
    return re.sub(re.escape(find), rep, text, flags=re.IGNORECASE)


@bot.on_message(filters.command("setdest") & filters.group)
async def set_dest(_, m):
    if m.reply_to_message:
        chat_id = m.chat.id
        DEST["chat_id"] = chat_id
        await m.reply_text(f"✅ Destination chat saved: `{chat_id}`")
    else:
        await m.reply_text("Reply to a message in the destination chat with /setdest")


@bot.on_message(filters.command("setfind") & filters.group)
async def set_find(_, m):
    if len(m.command) < 2: return await m.reply_text("Usage: /setfind <text>")
    SETTINGS["find"] = m.text.split(" ", 1)[1]
    await m.reply_text(f"🔍 Find set to `{SETTINGS['find']}`")


@bot.on_message(filters.command("setreplace") & filters.group)
async def set_replace(_, m):
    if len(m.command) < 2: return await m.reply_text("Usage: /setreplace <text or ->")
    txt = m.text.split(" ", 1)[1].strip()
    SETTINGS["replace"] = "" if txt == "-" else txt
    await m.reply_text(f"✏️ Replace set to `{SETTINGS['replace'] or '[remove]'}`")


@bot.on_message(filters.command("status") & filters.group)
async def status(_, m):
    msg = (f"Find: `{SETTINGS['find']}`\n"
           f"Replace: `{SETTINGS['replace'] or '[remove]'}`\n"
           f"Destination: `{DEST['chat_id']}`\n"
           f"Running: `{STATE['running']}`")
    await m.reply_text(msg)


@bot.on_message(filters.command("stop") & filters.group)
async def stop(_, m):
    if STATE["running"]:
        STATE["stop"] = True
        await m.reply_text("🛑 Stop requested.")
    else:
        await m.reply_text("ℹ️ Nothing running.")


@bot.on_message(filters.command("startclean") & filters.group)
async def startclean(client, m):
    if not DEST["chat_id"]:
        return await m.reply_text("⚠️ Set destination first with /setdest (reply to any msg there).")

    rng = RANGE_RE.search(m.text)
    if not rng:
        return await m.reply_text("Usage: /startclean 5-425")
    start, end = int(rng.group(1)), int(rng.group(2))
    total = end - start + 1
    if STATE["running"]: return await m.reply_text("⚠️ Already running.")
    STATE["running"] = True; STATE["stop"] = False
    await m.reply_text(f"🚀 Starting copy {start}-{end} ({total} msgs).")

    copied, skipped = 0, 0
    for msg_id in range(start, end + 1):
        if STATE["stop"]: break
        try:
            msg = await client.get_messages(m.chat.id, msg_id)
            cap = clean_caption(msg.caption or msg.text)
            await msg.copy(DEST["chat_id"], caption=cap)
            copied += 1
        except FloodWait as fw:
            await asyncio.sleep(fw.value + 1)
        except Exception as e:
            print("copy err", e)
            skipped += 1
        await asyncio.sleep(1.2)
        if copied % 20 == 0:
            try: await m.reply_text(f"Progress: {copied}/{total}")
            except: pass

    await m.reply_text(f"✅ Done. Copied: {copied}, Skipped: {skipped}")
    STATE["running"] = False; STATE["stop"] = False


async def main():
    await bot.start()
    print("🤖 Cleaner bot started.")
    import threading; threading.Thread(target=lambda: flask_app.run(host="0.0.0.0", port=PORT, use_reloader=False)).start()
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
    