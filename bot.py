#!/usr/bin/env python3
"""
Render-ready Auto Cleaner Bot (Pyrogram master, pre-clean copy, auto-create topic)

Environment variables required:
 - API_ID
 - API_HASH
 - BOT_TOKEN
 - PORT (optional, default 10000)

Commands (use in-group topics):
 - /setfind <text>
 - /setreplace <text|->   ('-' removes)
 - /botherestart [<thread_id>]   -> set or create destination topic
 - /botstartclean <start>-<end>  -> run copy from current topic to destination
 - /stop
 - /status
"""

import os
import re
import asyncio
import time
from flask import Flask
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, RPCError

# ---- Env / Config ----
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))

COPY_DELAY = float(os.getenv("COPY_DELAY", 1.5))   # safe default, tuneable
PROGRESS_EVERY = int(os.getenv("PROGRESS_EVERY", 20))

# Runtime settings (no persistence)
user_settings = {"find": "@ProfessorSarkari", "replace": "", "active": True}
destination = {"chat_id": None, "thread_id": None, "title": None}
current_job = {"running": False, "stop": False, "progress": {"copied": 0, "skipped": 0, "total": 0}}

RANGE_RE = re.compile(r"(\d+)\s*-\s*(\d+)")
app_flask = Flask("keepalive")
bot = Client("auto_cleaner_render", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


# ---- Flask keepalive (for UptimeRobot) ----
@app_flask.route("/")
def home():
    return "🤖 Auto Cleaner Bot (Render) — running"


@app_flask.route("/health")
def health():
    return "✅ OK"


# ---- Helpers ----
def parse_range_arg(text: str):
    m = RANGE_RE.search(text)
    if not m:
        return None
    s, e = int(m.group(1)), int(m.group(2))
    if s <= 0 or e < s:
        return None
    return s, e


def clean_text_case_insensitive(original: str, find_word: str, replace_with: str):
    if not original or not find_word:
        return original
    try:
        # Try exact-case first, else case-insensitive
        if find_word in original:
            return original.replace(find_word, replace_with)
        return re.sub(re.escape(find_word), replace_with, original, flags=re.IGNORECASE)
    except Exception:
        return original


async def safe_sleep_for_flood(fw_exc):
    # Extract seconds (Pyrogram's FloodWait uses .value or .x or .seconds)
    secs = getattr(fw_exc, "x", None) or getattr(fw_exc, "seconds", None) or getattr(fw_exc, "value", None)
    try:
        secs = int(secs)
    except Exception:
        secs = 5
    print(f"[floodwait] sleeping {secs + 1}s")
    await asyncio.sleep(secs + 1)


# ---- Commands ----
@bot.on_message(filters.command("setfind") & filters.group)
async def cmd_setfind(client, message):
    if len(message.command) < 2:
        await message.reply_text("Usage: /setfind <text>")
        return
    val = message.text.split(" ", 1)[1].strip()
    user_settings["find"] = val
    await message.reply_text(f"✅ Find set to: `{val}`")


@bot.on_message(filters.command("setreplace") & filters.group)
async def cmd_setreplace(client, message):
    if len(message.command) < 2:
        await message.reply_text("Usage: /setreplace <text>  (use '-' to remove)")
        return
    val = message.text.split(" ", 1)[1].strip()
    user_settings["replace"] = "" if val == "-" else val
    await message.reply_text(f"✏️ Replace set to: `{user_settings['replace'] or '[REMOVE]'}`")


@bot.on_message(filters.command("status") & filters.group)
async def cmd_status(client, message):
    dest = destination
    dest_text = f"chat={dest['chat_id']} thread={dest['thread_id']} title={dest.get('title')}" if dest["chat_id"] else "Not set"
    running = current_job["running"]
    await message.reply_text(
        f"Find: `{user_settings['find']}`\n"
        f"Replace: `{user_settings['replace'] or '[REMOVE]'}`\n"
        f"Destination: {dest_text}\n"
        f"Job running: {running}"
    )


@bot.on_message(filters.command("stop") & filters.group)
async def cmd_stop(client, message):
    if current_job["running"]:
        current_job["stop"] = True
        await message.reply_text("🛑 Stop requested. Will finish current message then stop.")
    else:
        await message.reply_text("ℹ️ No running job.")


# botherestart: auto-detect or create topic; optional manual override
@bot.on_message(filters.command("botherestart") & filters.group)
async def cmd_botherestart(client, message):
    chat_id = message.chat.id
    parts = message.text.strip().split()

    # manual override: /botherestart 555
    if len(parts) >= 2 and parts[1].isdigit():
        thread = int(parts[1])
        destination.update({"chat_id": chat_id, "thread_id": thread, "title": f"topic_{thread}"})
        await message.reply_text(f"✅ Destination set manually. chat={chat_id} thread={thread}")
        return

    # If message has message_thread_id (Render + pyrogram master)
    thread_id = getattr(message, "message_thread_id", None)
    if thread_id:
        destination.update({"chat_id": chat_id, "thread_id": thread_id, "title": f"topic_{thread_id}"})
        await message.reply_text(f"✅ Destination set to current topic (thread {thread_id})")
        return

    # Otherwise: try to create a topic automatically
    # Use a best-effort name: "<chat_title>_Cleaned" or timestamp
    chat_title = (await client.get_chat(chat_id)).title or "Cleaned_Topic"
    dest_name = f"{chat_title}_Cleaned"
    try:
        res = await client.create_forum_topic(chat_id=chat_id, name=dest_name)
        new_thread_id = getattr(res, "message_thread_id", None) or getattr(res, "id", None) or None
        if new_thread_id:
            destination.update({"chat_id": chat_id, "thread_id": new_thread_id, "title": dest_name})
            await message.reply_text(f"🆕 Created topic `{dest_name}` (thread {new_thread_id}) and set as destination.")
            return
    except Exception as e:
        await message.reply_text(f"⚠️ Could not auto-create topic: {e}")

    # Last-resort: instruct user to call with manual id
    await message.reply_text(
        "⚠️ Could not detect or create topic automatically. Please run:\n"
        "`/botherestart <topicStarterId>`\n"
        "You can get the starter ID from the topic link like: https://t.me/c/<chatnum>/<starterId>"
    )


@bot.on_message(filters.command("botstartclean") & filters.group)
async def cmd_botstartclean(client, message):
    if not destination["chat_id"] or not destination["thread_id"]:
        return await message.reply_text("⚠️ Destination not set. Run /botherestart in the destination topic (or /botherestart <id>) first.")

    if len(message.command) < 2:
        return await message.reply_text("Usage: /botstartclean <start>-<end>")

    rng = parse_range_arg = parse_range = None
    m = RANGE_RE.search(message.text)
    if not m:
        return await message.reply_text("❌ Wrong range format. Example: /botstartclean 5-425")
    start, end = int(m.group(1)), int(m.group(2))
    if start <= 0 or end < start:
        return await message.reply_text("❌ Invalid range.")

    src_chat = message.chat.id
    src_thread = getattr(message, "message_thread_id", None)  # may be None

    if current_job["running"]:
        return await message.reply_text("❌ Another job is running. Use /stop first.")

    total = end - start + 1
    current_job["running"] = True
    current_job["stop"] = False
    current_job["progress"] = {"copied": 0, "skipped": 0, "total": total}

    await message.reply_text(f"🚀 Starting copy {start} → {end} ({total} msgs) to topic {destination['thread_id']}")
    # background task
    asyncio.create_task(run_copy(client, message, src_chat, src_thread, start, end))


# ---- worker: pre-clean + copy into topic (uses message_thread_id param) ----
async def run_copy(client, trigger_message, src_chat_id, src_thread, start_id, end_id):
    dest_chat = destination["chat_id"]
    dest_thread = destination["thread_id"]
    find = user_settings["find"]
    replace = user_settings["replace"]
    total = end_id - start_id + 1
    copied = skipped = 0
    last_post = 0

    print(f"[run_copy] Starting {start_id}-{end_id} from {src_chat_id}/{src_thread} -> {dest_chat}/{dest_thread}")
    try:
        for msg_id in range(start_id, end_id + 1):
            if current_job["stop"]:
                await trigger_message.reply_text("🛑 Stop requested — aborting job.")
                break

            # fetch original message
            try:
                orig = await client.get_messages(src_chat_id, msg_id)
            except Exception as e:
                print(f"[run_copy] get_messages error for {msg_id}: {e}")
                orig = None

            if not orig:
                skipped += 1
                await asyncio.sleep(0.05)
                continue

            # prepare cleaned caption/text (if any)
            orig_text = orig.caption or orig.text or None
            new_caption = orig_text
            if orig_text and find and find.lower() in orig_text.lower():
                new_caption = clean_text_case_insensitive(orig_text, find, replace)

            # copy the message into destination topic with the cleaned caption
            try:
                resp = await client.copy_message(
                    chat_id=dest_chat,
                    from_chat_id=src_chat_id,
                    message_id=orig.message_id,
                    caption=new_caption,
                    message_thread_id=dest_thread
                )
            except FloodWait as fw:
                await safe_sleep_for_flood(fw)
                # retry once
                try:
                    resp = await client.copy_message(
                        chat_id=dest_chat,
                        from_chat_id=src_chat_id,
                        message_id=orig.message_id,
                        caption=new_caption,
                        message_thread_id=dest_thread
                    )
                except Exception as e:
                    print(f"[run_copy] copy retry failed for {msg_id}: {e}")
                    skipped += 1
                    await asyncio.sleep(COPY_DELAY)
                    continue
            except RPCError as rpc:
                print(f"[run_copy] RPCError copying {msg_id}: {rpc}")
                skipped += 1
                await asyncio.sleep(COPY_DELAY)
                continue
            except Exception as e:
                print(f"[run_copy] Unexpected copy error for {msg_id}: {e}")
                skipped += 1
                await asyncio.sleep(COPY_DELAY)
                continue

            copied += 1
            current_job["progress"]["copied"] = copied
            current_job["progress"]["skipped"] = skipped

            # console log
            if copied % 5 == 0:
                print(f"[run_copy] Progress: {copied}/{total} (skipped {skipped})")

            # post small progress to destination every PROGRESS_EVERY messages
            if copied - last_post >= PROGRESS_EVERY:
                try:
                    await client.send_message(dest_chat, f"📦 Progress: {copied}/{total} copied.", message_thread_id=dest_thread)
                except Exception as e:
                    print(f"[run_copy] Failed to post progress in dest: {e}")
                last_post = copied

            # safe delay between copies
            await asyncio.sleep(COPY_DELAY)

        # done
        final_msg = f"✅ Copy job finished. Copied: {copied}. Skipped: {skipped}."
        try:
            await trigger_message.reply_text(final_msg)
        except Exception:
            pass
        try:
            await client.send_message(dest_chat, final_msg, message_thread_id=dest_thread)
        except Exception:
            pass

    except Exception as e:
        print(f"[run_copy] Fatal error: {e}")
        try:
            await trigger_message.reply_text(f"❌ Job failed: {e}")
        except Exception:
            pass
    finally:
        current_job["running"] = False
        current_job["stop"] = False
        current_job["progress"] = {"copied": 0, "skipped": 0, "total": 0}
        print("[run_copy] Finished.")


# ---- live cleaning (optional) for new messages ----
@bot.on_message(filters.group)
async def live_clean_handler(client, message):
    # only when `active`
    if not user_settings["active"]:
        return
    try:
        text = message.caption or message.text or ""
        find = user_settings["find"]
        rep = user_settings["replace"]
        if find and find.lower() in text.lower():
            # We will copy-and-edit in-place: create bot-owned copy in same thread then delete original
            # Use message_thread_id if available to keep inside thread
            try:
                new = await client.copy_message(chat_id=message.chat.id, from_chat_id=message.chat.id, message_id=message.message_id, message_thread_id=getattr(message, "message_thread_id", None))
            except TypeError:
                new = await client.copy_message(chat_id=message.chat.id, from_chat_id=message.chat.id, message_id=message.message_id)
            # edit new message's caption/text (may incur small edit-rate limits for live cases)
            new_content = clean_text_case_insensitive(text, find, rep)
            try:
                if new.caption is not None:
                    await new.edit_caption(new_content)
                else:
                    await new.edit_text(new_content)
                await message.delete()
            except FloodWait as fw:
                await safe_sleep_for_flood(fw)
            except Exception as e:
                print(f"[live_clean] edit failed: {e}")
    except Exception as e:
        print(f"[live_clean] general error: {e}")


# ---- Boot: run Flask + Pyrogram concurrently on asyncio loop ----
async def main():
    # start pyrogram client
    await bot.start()
    print("🤖 Caption Cleaner Bot started (Pyrogram).")

    # run Flask app in background thread using asyncio's to_thread
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, lambda: app_flask.run(host="0.0.0.0", port=PORT, use_reloader=False))

    # keep running until cancelled
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
