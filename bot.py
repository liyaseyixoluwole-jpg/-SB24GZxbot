"""
Smart Timer Bot - Telegram Bot
Features:
- Countdown timer with custom duration
- Start / Pause / Resume / Cancel
- Timer notifications
- Multiple active timers per user
"""

import os
import re
import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ---------- Config ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
DATA_FILE = os.environ.get("DATA_FILE", "timers.json")
CHECK_INTERVAL = 1

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("smart-timer-bot")


# ---------- Storage ----------
class TimerStore:
    def __init__(self, path: str):
        self.path = path
        self.timers: Dict[str, dict] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.timers = json.load(f)
            except Exception as e:
                logger.warning(f"Could not load store: {e}")
                self.timers = {}

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.timers, f)
        except Exception as e:
            logger.warning(f"Could not save store: {e}")

    def add(self, timer_id: str, data: dict):
        self.timers[timer_id] = data
        self.save()

    def get(self, timer_id: str) -> Optional[dict]:
        return self.timers.get(timer_id)

    def remove(self, timer_id: str):
        if timer_id in self.timers:
            del self.timers[timer_id]
            self.save()

    def user_timers(self, user_id: int) -> List[Tuple[str, dict]]:
        return [(tid, t) for tid, t in self.timers.items() if t["user_id"] == user_id]


store = TimerStore(DATA_FILE)


# ---------- Helpers ----------
DURATION_RE = re.compile(
    r"^(?:(\d+)\s*h(?:ours?|rs?)?)?\s*"
    r"(?:(\d+)\s*m(?:in(?:utes?)?)?)?\s*"
    r"(?:(\d+)\s*s(?:ec(?:onds?)?)?)?$",
    re.IGNORECASE,
)


def parse_duration(text: str) -> Optional[int]:
    text = text.strip().lower().replace(",", " ")
    if not text:
        return None
    if text.isdigit():
        return int(text)
    match = DURATION_RE.match(text)
    if not match:
        return None
    h, m, s = match.groups()
    if not any([h, m, s]):
        return None
    total = int(h or 0) * 3600 + int(m or 0) * 60 + int(s or 0)
    return total if total > 0 else None


def human_time(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s}s")
    return " ".join(parts)


def new_timer_id(user_id: int) -> str:
    return f"{user_id}_{int(datetime.utcnow().timestamp() * 1000)}"


def build_timer_keyboard(timer_id: str, paused: bool) -> InlineKeyboardMarkup:
    if paused:
        row = [
            InlineKeyboardButton("▶️ Resume", callback_data=f"resume:{timer_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{timer_id}"),
        ]
    else:
        row = [
            InlineKeyboardButton("⏸ Pause", callback_data=f"pause:{timer_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{timer_id}"),
        ]
    return InlineKeyboardMarkup([row])


# ---------- Commands ----------
WELCOME = (
    "👋 *Welcome to Smart Timer Bot!*\n\n"
    "I can run multiple countdown timers for you.\n\n"
    "*Commands:*\n"
    "• `/start` — show this menu\n"
    "• `/timer <duration>` — start a timer\n"
    "   Examples: `/timer 5m`, `/timer 1h30m`, `/timer 90s`\n"
    "• `/list` — show your active timers\n"
    "• `/cancel <id>` — cancel a specific timer\n"
    "• `/cancelall` — cancel all your timers\n"
    "• `/help` — help\n\n"
    "Each timer has buttons to *Pause*, *Resume*, or *Cancel*."
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME, parse_mode=ParseMode.MARKDOWN)


async def cmd_timer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "⚠️ Please provide a duration.\n"
            "Examples: `/timer 5m`, `/timer 1h30m`, `/timer 90s`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    duration_text = " ".join(context.args)
    seconds = parse_duration(duration_text)
    if not seconds:
        await update.message.reply_text(
            "❌ Invalid duration. Try formats like `5m`, `1h30m`, `90s`, or plain seconds.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    user = update.effective_user
    chat_id = update.effective_chat.id
    timer_id = new_timer_id(user.id)
    end_ts = (datetime.utcnow() + timedelta(seconds=seconds)).timestamp()

    data = {
        "user_id": user.id,
        "chat_id": chat_id,
        "duration": seconds,
        "remaining": seconds,
        "end_ts": end_ts,
        "paused": False,
        "message_id": None,
        "started_at": datetime.utcnow().timestamp(),
        "label": duration_text,
    }
    store.add(timer_id, data)

    msg = await update.message.reply_text(
        f"⏱ *Timer started!*\n\n"
        f"Duration: *{human_time(seconds)}*\n"
        f"ID: `{timer_id}`\n\n"
        f"⏳ Time remaining: *{human_time(seconds)}*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=build_timer_keyboard(timer_id, paused=False),
    )

    data["message_id"] = msg.message_id
    store.add(timer_id, data)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    timers = store.user_timers(user.id)
    if not timers:
        await update.message.reply_text("📭 You have no active timers.")
        return

    lines = ["📋 *Your active timers:*\n"]
    for tid, t in timers:
        remaining = t["remaining"] if t["paused"] else max(
            0, int(t["end_ts"] - datetime.utcnow().timestamp())
        )
        status = "⏸ Paused" if t["paused"] else "▶️ Running"
        lines.append(
            f"• *{human_time(t['duration'])}* — {human_time(remaining)} left — {status}\n"
            f"  ID: `{tid}`"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Usage: `/cancel <timer_id>`", parse_mode=ParseMode.MARKDOWN
        )
        return
    timer_id = context.args[0]
    t = store.get(timer_id)
    if not t or t["user_id"] != update.effective_user.id:
        await update.message.reply_text("❌ Timer not found.")
        return
    store.remove(timer_id)
    await update.message.reply_text("✅ Timer cancelled.")
    try:
        if t.get("message_id"):
            await context.bot.edit_message_text(
                chat_id=t["chat_id"],
                message_id=t["message_id"],
                text=f"❌ *Timer cancelled.*\n\nDuration was: {human_time(t['duration'])}",
                parse_mode=ParseMode.MARKDOWN,
            )
    except Exception:
        pass


async def cmd_cancelall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    timers = store.user_timers(user.id)
    if not timers:
        await update.message.reply_text("📭 You have no active timers.")
        return
    count = 0
    for tid, _ in timers:
        store.remove(tid)
        count += 1
    await update.message.reply_text(f"✅ Cancelled {count} timer(s).")


# ---------- Callback buttons ----------
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        action, timer_id = query.data.split(":", 1)
    except ValueError:
        return

    t = store.get(timer_id)
    if not t or t["user_id"] != query.from_user.id:
        await query.edit_message_text("❌ Timer no longer active.")
        return

    now = datetime.utcnow().timestamp()

    if action == "pause":
        if t["paused"]:
            return
        t["remaining"] = max(0, int(t["end_ts"] - now))
        t["paused"] = True
        store.add(timer_id, t)
        await query.edit_message_text(
            f"⏸ *Timer paused.*\n\n"
            f"Time left: *{human_time(t['remaining'])}*\n"
            f"ID: `{timer_id}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_timer_keyboard(timer_id, paused=True),
        )

    elif action == "resume":
        if not t["paused"]:
            return
        t["end_ts"] = now + t["remaining"]
        t["paused"] = False
        store.add(timer_id, t)
        await query.edit_message_text(
            f"▶️ *Timer resumed.*\n\n"
            f"Duration: *{human_time(t['remaining'])}*\n"
            f"ID: `{timer_id}`\n\n"
            f"⏳ Time remaining: *{human_time(t['remaining'])}*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_timer_keyboard(timer_id, paused=False),
        )

    elif action == "cancel":
        store.remove(timer_id)
        await query.edit_message_text(
            f"❌ *Timer cancelled.*\n\nDuration was: {human_time(t['duration'])}",
            parse_mode=ParseMode.MARKDOWN,
        )


# ---------- Background worker ----------
async def timer_worker(app: Application):
    while True:
        try:
            now = datetime.utcnow().timestamp()
            for timer_id, t in list(store.timers.items()):
                if t["paused"]:
                    continue
                if now >= t["end_ts"]:
                    try:
                        await app.bot.send_message(
                            chat_id=t["chat_id"],
                            text=(
                                f"🔔 *Timer finished!*\n\n"
                                f"Duration: *{human_time(t['duration'])}*\n"
                                f"⏰ Time's up!"
                            ),
                            parse_mode=ParseMode.MARKDOWN,
                        )
                    except Exception as e:
                        logger.warning(f"Failed to notify: {e}")

                    if t.get("message_id"):
                        try:
                            await app.bot.edit_message_text(
                                chat_id=t["chat_id"],
                                message_id=t["message_id"],
                                text=(
                                    f"✅ *Timer finished!*\n\n"
                                    f"Duration: {human_time(t['duration'])}"
                                ),
                                parse_mode=ParseMode.MARKDOWN,
                            )
                        except Exception:
                            pass

                    store.remove(timer_id)
        except Exception as e:
            logger.exception(f"Worker error: {e}")

        await asyncio.sleep(CHECK_INTERVAL)


# ---------- App lifecycle ----------
async def on_startup(app: Application):
    app.create_task(timer_worker(app))
    logger.info("Timer worker started.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling an update:", exc_info=context.error)


def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "❌ BOT_TOKEN is not set. Add it as an environment variable."
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("timer", cmd_timer))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("cancelall", cmd_cancelall))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(on_error)

    logger.info("🤖 Smart Timer Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
