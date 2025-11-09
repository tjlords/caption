#!/usr/bin/env python3
# Auto Cleaner Bot - Render Web Service + Flask keepalive (Pyrogram topic-ready)

import os, asyncio, re
from flask import Flask
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, RPCError

# ---- Environment variables ----
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))

# ---- Config ----
COPY_DELAY = 2.0
user_settings = {"find": "@ProfessorSarkari", "replace": "", "active": True}
destination = {"chat_id": None, "thread_id": None, "title": None}
current_job = {"running": False, "stop": False}
RANGE_RE = re.compile(r"(\d+)\s*-\s*(\d+)")

# ---- Flask keepalive ----
app_flask = Flask(__name__)

@app_flask.route("/")
def home():
    return "🤖 Caption Cleaner Bot is running on Render!"

@app_flask.route("/health")
def health():
    return "✅ OK"

# ---- Pyrogram client ----
bot = Client("caption_cleaner_render", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


# ========== COMMANDS ==========

@bot.on_message(filters.command("setfind"))
async def set_find(_, m):
    if len(m.command) < 2:
        return await m.reply("Usage: /setfind <text>")
    user_settings["find"] = m.text.split(" ", 1)[1]
    await m.reply(f"✅ Find set to: `{user_settings['find']}`")

@bot.on_message(filters.command("setreplace"))
async def set_replace(_, m):
    if len(m.command) < 2:
        return await m.reply("Usage: /setreplace <text|-`")
    val = m.text.split(" ", 1)[1].strip()
    user_settings["replace"] = "" if val == "-" else val
    await m.reply(f"✏️ Replace set to: `{user_settings['replace'] or '[REMOVE]'}`")

@bot.on_message(filters.command("status"))
async def status(_, m):
    d = destination
    txt = (
        f"Find: `{user_settings['find']}`\n"
        f"Replace: `{user_settings['replace'] or '[REMOVE]'}`\n"
        f"Dest chat: {d['chat_id']}\n"
        f"Dest thread: {d['thread_id']}\n"
        f"Running: {current_job['running']}"
    )
    await m.reply(txt)

@bot.on_message(filters.command("stop"))
async def stop(_, m):
    if current_job["running"]:
        current_job["stop"] = True
        await m.reply("🛑 Stop requested.")
    else:
        await m.reply("ℹ️ No job running.")


# ---- botherestart ----
@bot.on_message(filters.command("botherestart") & filters.group)
async def botherestart(client, message):
    """
    /botherestart               → auto detect thread
    /botherestart <thread_id>   → manual override
    """
    chat_id = message.chat.id
    parts = message.text.strip().split()

    # manual override
    if len(parts) >= 2 and parts[1].isdigit():
        thread_id = int(parts[1])
        destination.update({"chat_id": chat_id, "thread_id": thread_id, "title": f"topic_{thread_id}"})
        return await message.reply(f"✅ Destination topic set manually.\nChat: `{chat_id}`\nThread: `{thread_id}`")

    # detect automatically
    thread_id = getattr(message, "message_thread_id", None)
    if not thread_id:
        # auto create new topic if allowed
        try:
            topic = await client.create_forum_topic(chat_id, name="Cleaned_Topic")
            thread_id = topic.message_thread_id
            await message.reply(f"🆕 Created new topic `Cleaned_Topic` (id: {thread_id})")
        except Exception as e:
            await message.reply(f"⚠️ Could not detect or create topic automatically: {e}")
            return

    destination.update({"chat_id": chat_id, "thread_id": thread_id, "title": f"topic_{thread_id}"})
    await message.reply(f"✅ Destination topic set automatically.\nChat: `{chat_id}`\nThread: `{thread_id}`")


# ---- botstartclean ----
@bot.on_message(filters.command("botstartclean") & filters.group)
async def botstartclean(client, message):
    if not destination["chat_id"]:
        return await message.reply("⚠️ Run /botherestart in destination topic first.")
    if len(message.command) < 2:
        return await message.reply("Usage: /botstartclean 5-425")
    rg = RANGE_RE.search(message.text)
    if not rg:
        return await message.reply("❌ Wrong range format. Example: /botstartclean 5-425")
    start, end = int(rg.group(1)), int(rg.group(2))
    src_chat = message.chat.id
    src_thread = getattr(message, "message_thread_id", None)

    if current_job["running"]:
        return await message.reply("❌ Another job running. Use /stop first.")

    current_job.update({"running": True, "stop": False})
    await message.reply(f"🚀 Copying {start}-{end} → topic {destination['thread_id']}")
    asyncio.create_task(run_copy(client, message, src_chat, src_thread, start, end))


# ========== CORE WORKER ==========

async def run_copy(c, trig, chat, src_thread, start, end):
    dest = destination
    find, rep = user_settings["find"], user_settings["replace"]
    total = end - start + 1
    copied = skipped = 0
    last_progress_post = 0

    print(f"[copy] {start}-{end} from {chat}/{src_thread} → {dest['chat_id']}/{dest['thread_id']}")
    await trig.reply(f"🚀 Starting {start}-{end} ({total} msgs) → topic {dest['thread_id']}")

    try:
        for mid in range(start, end + 1):
            if current_job["stop"]:
                await trig.reply("🛑 Job stop requested — stopping now.")
                break

            try:
                msg = await c.get_messages(chat, mid)
            except Exception:
                msg = None

            if not msg:
                skipped += 1
                await asyncio.sleep(0.05)
                continue

            caption = msg.caption or msg.text or None
            new_caption = caption
            if caption and find and find.lower() in caption.lower():
                try:
                    new_caption = re.sub(re.escape(find), rep, caption, flags=re.I)
                except Exception:
                    new_caption = caption

            try:
                await c.copy_message(
                    chat_id=dest["chat_id"],
                    from_chat_id=chat,
                    message_id=msg.id,
                    caption=new_caption,
                    message_thread_id=dest["thread_id"]
                )
            except FloodWait as fw:
                wait_for = int(getattr(fw, "x", getattr(fw, "seconds", fw.value)))
                print(f"[copy] FloodWait {wait_for}s at msg {mid}")
                await asyncio.sleep(wait_for + 1)
                continue
            except RPCError as rpc:
                print(f"[copy] RPCError at msg {mid}: {rpc}")
                skipped += 1
                await asyncio.sleep(COPY_DELAY)
                continue
            except Exception as e:
                print(f"[copy] Error copying msg {mid}: {e}")
                skipped += 1
                await asyncio.sleep(COPY_DELAY)
                continue

            copied += 1
            if copied % 5 == 0:
                print(f"[copy] Progress {copied}/{total}")
            if copied - last_progress_post >= 20:
                try:
                    await c.send_message(
                        dest["chat_id"],
                        f"📦 Progress: {copied}/{total} copied.",
                        message_thread_id=dest["thread_id"]
                    )
                except Exception:
                    pass
                last_progress_post = copied

            await asyncio.sleep(COPY_DELAY)

        msg_done = f"✅ Done. Copied: {copied}. Skipped: {skipped}."
        await trig.reply(msg_done)
        try:
            await c.send_message(dest["chat_id"], msg_done, message_thread_id=dest["thread_id"])
        except Exception:
            pass

    except Exception as e:
        print(f"[copy] Job error: {e}")
        await trig.reply(f"❌ Job failed: {e}")
    finally:
        current_job.update({"running": False, "stop": False})
        print(f"[copy] Finished {copied}/{total}")


# ========== RUN BOTH ==========

async def main():
    # Run Flask in thread and Pyrogram concurrently
    loop = asyncio.get_event_loop()
    loop.create_task(bot.start())
    print("🤖 Caption Cleaner Bot started successfully.")
    app_flask.run(host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    asyncio.run(main())
    