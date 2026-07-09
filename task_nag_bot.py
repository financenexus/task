"""
Task Nag Bot v2 — talk to it in plain language.

You say things like:
    "I have a contract to send before 5pm"
    "remind me to call the dentist tomorrow morning"
    "I'm done with the contract"

No commands, no IDs, no DONE keyword. The bot uses Claude to figure out:
  - are you adding a task (and when is it due)?
  - are you telling it a task is finished (and which one)?
  - or neither (then it just nudges you to say one of those two things)

NAG BEHAVIOR
------------
As the deadline approaches, pings get more frequent:
  - more than 2h left   -> every 45 min
  - 30min - 2h left     -> every 20 min
  - 10-30 min left      -> every 5 min
  - under 10 min left / overdue -> every 2 min, forever, until confirmed done

SETUP
-----
1. Telegram: talk to @BotFather -> /newbot -> copy token
2. Get a free NVIDIA API key: go to build.nvidia.com, sign up (free),
   open any model page (e.g. "llama-3.3-70b-instruct"), click "Get API Key".
   Free tier gives generous monthly credits at no cost for personal use.
3. pip install python-telegram-bot==21.4 openai --break-system-packages
   (NVIDIA's API is OpenAI-compatible, so we reuse the openai package)
4. export TELEGRAM_BOT_TOKEN="your-telegram-token"
5. export NVIDIA_API_KEY="your-nvidia-key"
6. python task_nag_bot.py
7. Message your bot on Telegram, just talk normally.

Tasks persist in tasks.json next to this script.
"""

import json
import os
import logging
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_FILE = os.path.join(os.path.dirname(__file__), "tasks.json")

# NVIDIA NIM API - OpenAI-compatible, free tier
nim_client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ.get("NVIDIA_API_KEY"),
)
NIM_MODEL = "meta/llama-3.3-70b-instruct"

PARSE_SYSTEM_PROMPT = """You are the intent parser for a task-reminder bot. \
Given the user's message and their current open tasks, output ONLY a JSON object, \
nothing else, no markdown fences.

Schema:
{{
  "intent": "add" | "done" | "unclear",
  "task_text": string or null,       // short task description, only for intent=="add"
  "deadline_iso": string or null,    // ISO 8601 datetime, only for intent=="add". \
If the user gives a time like "5pm" assume today unless that time has already passed, \
then assume tomorrow. If no deadline is mentioned, use null.
  "matched_task_id": string or null  // only for intent=="done": the id of the open task \
this message refers to, matched by meaning/content, not exact wording. null if no confident match.
}}

Current datetime is: {now}
Open tasks (id: text): {open_tasks}
"""


# ---------------------------------------------------------------- storage --
def load_tasks():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)


def save_tasks(tasks):
    with open(DATA_FILE, "w") as f:
        json.dump(tasks, f, indent=2)


def next_task_id(tasks):
    if not tasks:
        return "1"
    return str(max(int(k) for k in tasks.keys()) + 1)


# ------------------------------------------------------------------- ai ---
def parse_message(user_text, open_tasks, now):
    open_tasks_str = ", ".join(f"{tid}: {t['text']}" for tid, t in open_tasks.items()) or "none"
    system = PARSE_SYSTEM_PROMPT.format(now=now.isoformat(), open_tasks=open_tasks_str)

    resp = nim_client.chat.completions.create(
        model=NIM_MODEL,
        max_tokens=300,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
    )
    raw = resp.choices[0].message.content.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"Could not parse model output: {raw}")
        return {"intent": "unclear", "task_text": None, "deadline_iso": None, "matched_task_id": None}


# --------------------------------------------------------------- nag core --
def next_interval_minutes(deadline_iso):
    """Given an ISO deadline (or None), decide minutes until the next nag."""
    if not deadline_iso:
        return 45  # no deadline given -> steady gentle nags
    deadline = datetime.fromisoformat(deadline_iso)
    remaining = (deadline - datetime.now(timezone.utc)).total_seconds() / 60.0
    if remaining > 120:
        return 45
    elif remaining > 30:
        return 20
    elif remaining > 10:
        return 5
    else:
        return 2  # under 10 min left, or overdue -> hammer every 2 min


async def schedule_nag(context, chat_id, tid, delay_minutes):
    context.job_queue.run_once(
        nag_job,
        when=timedelta(minutes=delay_minutes),
        data={"chat_id": chat_id, "task_id": tid},
        name=f"nag_{chat_id}_{tid}",
    )


async def nag_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    chat_id, tid = data["chat_id"], data["task_id"]

    tasks = load_tasks()
    task = tasks.get(chat_id, {}).get(tid)
    if not task:
        return  # already completed

    deadline_note = ""
    if task.get("deadline_iso"):
        deadline_note = f"\nDue: {task['deadline_iso']}"
    overdue = task.get("deadline_iso") and datetime.fromisoformat(task["deadline_iso"]) < datetime.now(timezone.utc)
    prefix = "🚨 OVERDUE" if overdue else "⏰ Still open"

    await context.bot.send_message(
        chat_id=int(chat_id),
        text=f"{prefix}: {task['text']}{deadline_note}\nJust tell me when it's done.",
    )

    delay = next_interval_minutes(task.get("deadline_iso"))
    await schedule_nag(context, chat_id, tid, delay)


# --------------------------------------------------------------- handlers --
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "I'm here. Just talk to me normally — no commands needed.\n\n"
        "Example: \"I have a contract to send before 5pm\"\n"
        "When it's done, just say so: \"finished the contract\""
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user_text = update.message.text.strip()
    now = datetime.now(timezone.utc)

    tasks = load_tasks()
    open_tasks = tasks.get(chat_id, {})

    parsed = parse_message(user_text, open_tasks, now)
    intent = parsed.get("intent")

    if intent == "add":
        tasks.setdefault(chat_id, {})
        tid = next_task_id(tasks[chat_id])
        tasks[chat_id][tid] = {
            "text": parsed.get("task_text") or user_text,
            "deadline_iso": parsed.get("deadline_iso"),
            "created": now.isoformat(),
        }
        save_tasks(tasks)

        deadline_msg = f" — due {parsed['deadline_iso']}" if parsed.get("deadline_iso") else ""
        await update.message.reply_text(f"Got it: {tasks[chat_id][tid]['text']}{deadline_msg}. I will not let you forget.")

        first_delay = next_interval_minutes(parsed.get("deadline_iso"))
        await schedule_nag(context, chat_id, tid, first_delay)

    elif intent == "done":
        tid = parsed.get("matched_task_id")
        if not tid or tid not in open_tasks:
            await update.message.reply_text(
                "I hear you saying something's done, but I'm not sure which task. "
                "Can you say it a bit more specifically?"
            )
            return

        finished = open_tasks.pop(tid)
        save_tasks(tasks)
        for job in context.job_queue.get_jobs_by_name(f"nag_{chat_id}_{tid}"):
            job.schedule_removal()

        await update.message.reply_text(f"Confirmed done: {finished['text']} ✅")

    else:
        if open_tasks:
            open_list = "\n".join(f"- {t['text']}" for t in open_tasks.values())
            await update.message.reply_text(
                "Not sure what you mean. You still have open:\n" + open_list +
                "\n\nTell me a new task with a deadline, or tell me one of these is done."
            )
        else:
            await update.message.reply_text(
                "Not sure what you mean. Try telling me a task, e.g. "
                "\"I need to send the contract before 5pm\"."
            )


def main():
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not tg_token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN env var first.")
    if not os.environ.get("NVIDIA_API_KEY"):
        raise SystemExit("Set NVIDIA_API_KEY env var first.")

    app = ApplicationBuilder().token(tg_token).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
