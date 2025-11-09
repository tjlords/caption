import os, re, asyncio, requests, aiofiles
from flask import Flask
from pyrogram import Client, filters
from pyrogram.errors import FloodWait

# ======== ENV CONFIG ========
API_ID = int(os.getenv("API_ID") or 11843091)
API_HASH = os.getenv("API_HASH") or "be955ff462011615097f96745b3627f3"
BOT_TOKEN = os.getenv("BOT_TOKEN") or "8127293382:AAHnBJGwOwlgD2Fe8R-6iimUOyhuoMxw6wU"
OWNER_ID = int(os.getenv("OWNER_ID") or 5891678566)  # your own Telegram ID
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ======== INIT ========
bot = Client("caption_cleaner_render", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
app = Flask(__name__)

FIND_TEXT, REPLACE_TEXT = "@ProfessorSarkari", ""
DEST_TOPIC_ID, FORWARD_DEST, RUNNING, STOP_FLAG = None, None, False, False
RANGE_RE = re.compile(r"(\d+)-(\d+)")


# ======== HELPERS ========
def is_owner(func):
    async def wrapper(client, msg):
        if msg.from_user and msg.from_user.id != OWNER_ID:
            return await msg.reply_text("🚫 Not authorized.")
        return await func(client, msg)
    return wrapper


def tg_api(method, data):
    try:
        r = requests.post(f"{BASE_URL}/{method}", json=data, timeout=30)
        return r.json()
    except Exception as e:
        print("Bot API error:", e)
        return None


def clean_caption(text):
    if not text: return text
    return re.sub(re.escape(FIND_TEXT), REPLACE_TEXT, text, flags=re.IGNORECASE)


async def rename_pdf(client, msg, caption, dest_topic):
    """Download PDF → rename → reupload"""
    try:
        file = await client.download_media(msg)
        new_name = os.path.basename(file).replace(FIND_TEXT, REPLACE_TEXT)
        new_path = os.path.join(os.path.dirname(file), new_name)
        os.rename(file, new_path)
        await client.send_document(
            msg.chat.id, document=new_path, caption=caption, message_thread_id=dest_topic
        )
        os.remove(new_path)
        return True
    except Exception as e:
        print("rename_pdf error:", e)
        return False


# ======== BASIC COMMANDS ========
@bot.on_message(filters.command("start"))
@is_owner
async def start(_, m):
    await m.reply_text(
        "🤖 **Render Caption Cleaner Bot**\n\n"
        "✅ Works 24/7 with Render + Flask\n"
        "Commands:\n"
        "`/createtopic <name>` — Create forum topic\n"
        "`/setfind <word>` / `/setreplace <txt>`\n"
        "`/cleanrange 5-425` — Clean captions & rename PDFs\n"
        "`/forwardset` — Set current topic as destination\n"
        "`/forwardstart 5-425` — Start forwarding\n"
        "`/stop`, `/forwardstop`, `/status`\n"
        "Only owner can use this bot."
    )


@bot.on_message(filters.command("status"))
@is_owner
async def status(_, m):
    await m.reply_text(
        f"Find: `{FIND_TEXT}` | Replace: `{REPLACE_TEXT or '[REMOVE]'}`\n"
        f"DestTopic: `{DEST_TOPIC_ID}` | ForwardDest: `{FORWARD_DEST}`\nRunning: `{RUNNING}`"
    )


@bot.on_message(filters.command("stop"))
@is_owner
async def stop(_, m):
    global STOP_FLAG
    STOP_FLAG = True
    await m.reply_text("🛑 Stop signal sent.")


# ======== CLEAN RANGE ========
@bot.on_message(filters.command("setfind"))
@is_owner
async def set_find(_, m):
    global FIND_TEXT
    if len(m.command) < 2:
        return await m.reply_text("Usage: /setfind <text>")
    FIND_TEXT = m.text.split(" ", 1)[1].strip()
    await m.reply_text(f"🔍 Find text set to `{FIND_TEXT}`")


@bot.on_message(filters.command("setreplace"))
@is_owner
async def set_replace(_, m):
    global REPLACE_TEXT
    if len(m.command) < 2:
        return await m.reply_text("Usage: /setreplace <text or ->")
    val = m.text.split(" ", 1)[1].strip()
    REPLACE_TEXT = "" if val == "-" else val
    await m.reply_text(f"✏️ Replace set to `{REPLACE_TEXT or '[REMOVE]'}`")


@bot.on_message(filters.command("createtopic"))
@is_owner
async def createtopic(_, m):
    global DEST_TOPIC_ID
    if len(m.command) < 2:
        return await m.reply_text("Usage: /createtopic <name>")
    name = m.text.split(" ", 1)[1].strip()
    res = tg_api("createForumTopic", {"chat_id": m.chat.id, "name": name})
    if res and res.get("ok"):
        DEST_TOPIC_ID = res["result"]["message_thread_id"]
        await m.reply_text(f"🆕 Created topic `{name}`\nID: `{DEST_TOPIC_ID}` saved.")
    else:
        await m.reply_text(f"❌ Failed: `{res}`")


@bot.on_message(filters.command("cleanrange"))
@is_owner
async def cleanrange(client, m):
    global RUNNING, STOP_FLAG
    if RUNNING:
        return await m.reply_text("⚠️ Already running.")
    if not DEST_TOPIC_ID:
        return await m.reply_text("⚠️ No topic. Use /createtopic first.")
    rg = RANGE_RE.search(m.text)
    if not rg:
        return await m.reply_text("Usage: /cleanrange 5-425")

    start, end = map(int, rg.groups())
    RUNNING, STOP_FLAG = True, False
    total, copied, edited = end - start + 1, 0, 0
    await m.reply_text(f"🚀 Cleaning {total} messages...")

    for mid in range(start, end + 1):
        if STOP_FLAG: break
        try:
            src = await client.get_messages(m.chat.id, mid)
            if not src: continue
            cap = src.caption or src.text
            newcap = clean_caption(cap) if cap and FIND_TEXT.lower() in cap.lower() else cap

            # If PDF file — rename
            if src.document and src.document.file_name.endswith(".pdf"):
                ok = await rename_pdf(client, src, newcap, DEST_TOPIC_ID)
                if ok: edited += 1
            else:
                # Copy normally via Bot API
                cpy = tg_api("copyMessages", {
                    "chat_id": m.chat.id,
                    "from_chat_id": m.chat.id,
                    "message_thread_id": DEST_TOPIC_ID,
                    "message_ids": [mid]
                })
                if cap and FIND_TEXT.lower() in cap.lower() and cpy and cpy.get("ok"):
                    msg_id = cpy["result"][0]["message_id"]
                    tg_api("editMessageCaption", {
                        "chat_id": m.chat.id,
                        "message_id": msg_id,
                        "caption": newcap
                    })
                    edited += 1

            copied += 1
            if copied % 20 == 0:
                await m.reply_text(f"Progress: {copied}/{total} (edited {edited})")
            await asyncio.sleep(1.3)

        except FloodWait as fw:
            await asyncio.sleep(fw.value + 1)
        except Exception as e:
            print("clean err", e)
            await asyncio.sleep(1)

    RUNNING = False
    await m.reply_text(f"✅ Done! Copied {copied}, Edited {edited}")


# ======== FORWARD FEATURE ========
@bot.on_message(filters.command("forwardset"))
@is_owner
async def forwardset(_, m):
    global FORWARD_DEST
    if not m.message_thread_id:
        return await m.reply_text("⚠️ Use this inside a topic thread.")
    FORWARD_DEST = (m.chat.id, m.message_thread_id)
    await m.reply_text(f"✅ Forward destination set:\nChat `{m.chat.id}` | Topic `{m.message_thread_id}`")


@bot.on_message(filters.command("forwardstart"))
@is_owner
async def forwardstart(client, m):
    global RUNNING, STOP_FLAG
    if not FORWARD_DEST:
        return await m.reply_text("⚠️ No forward target. Use /forwardset in destination topic first.")
    rg = RANGE_RE.search(m.text)
    if not rg:
        return await m.reply_text("Usage: /forwardstart 5-425")
    chat_dest, topic_dest = FORWARD_DEST
    start, end = map(int, rg.groups())
    RUNNING, STOP_FLAG = True, False
    total, sent = end - start + 1, 0
    await m.reply_text(f"🚀 Forwarding {total} messages...")

    for mid in range(start, end + 1):
        if STOP_FLAG: break
        try:
            src = await client.get_messages(m.chat.id, mid)
            if not src: continue
            await src.copy(chat_id=chat_dest, message_thread_id=topic_dest)
            sent += 1
            if sent % 20 == 0:
                await m.reply_text(f"Progress: {sent}/{total}")
            await asyncio.sleep(1.2)
        except FloodWait as fw:
            await asyncio.sleep(fw.value + 1)
        except Exception as e:
            print("forward err", e)
            await asyncio.sleep(1)
    RUNNING = False
    await m.reply_text(f"✅ Forwarded {sent} messages successfully.")


@bot.on_message(filters.command("forwardstop"))
@is_owner
async def forwardstop(_, m):
    global STOP_FLAG
    STOP_FLAG = True
    await m.reply_text("🛑 Forwarding stopped.")


# ======== FLASK KEEP-ALIVE ========
@app.route("/")
def home(): return "🤖 Render Caption Cleaner Bot is running!"
@app.route("/health")
def health(): return "✅ OK"


# ======== MAIN ========
async def main():
    await asyncio.gather(bot.start(), asyncio.to_thread(app.run, host="0.0.0.0", port=10000))

if __name__ == "__main__":
    asyncio.run(main())
