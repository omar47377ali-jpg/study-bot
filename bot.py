import asyncio
import base64
import json
import logging
import os

from aiohttp import web
from anthropic import AsyncAnthropic
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("study-bot")

TOKEN = os.environ["TELEGRAM_TOKEN"]
MODEL = os.getenv("MODEL", "claude-sonnet-5-5")
NUM_Q = int(os.getenv("NUM_QUESTIONS", "5"))
PORT = int(os.getenv("PORT", "10000"))
ALLOWED = {int(x) for x in os.getenv("ALLOWED_IDS", "").split(",") if x.strip()}

client = AsyncAnthropic()
sessions: dict[int, dict] = {}
LETTERS = ["A", "B", "C", "D"]

SYSTEM = """You are a patient university tutor. The student sends you a page (image, PDF, or text).
1. Explain the content simply and clearly in Arabic (Jordanian-friendly Modern Arabic). Keep technical terms in English next to the Arabic. Use short paragraphs and no markdown symbols.
2. Write exactly NUM_Q multiple-choice questions that test real understanding of the page (not just memorization). Each has 4 options, exactly one correct.
Return ONLY valid JSON, no code fences, in this shape:
{"explanation": "...", "questions": [{"q": "...", "options": ["...", "...", "...", "..."], "answer": 0, "why": "short explanation of the correct answer"}]}
"answer" is the zero-based index of the correct option. Vary the position of the correct answer."""
SYSTEM = SYSTEM.replace("NUM_Q", str(NUM_Q))


def allowed(uid: int) -> bool:
    return not ALLOWED or uid in ALLOWED


def parse_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    data = json.loads(text[start : end + 1])
    good = []
    for q in data.get("questions", []):
        opts = q.get("options", [])
        ans = q.get("answer")
        if q.get("q") and 2 <= len(opts) <= 4 and isinstance(ans, int) and 0 <= ans < len(opts):
            q.setdefault("why", "")
            good.append(q)
    data["questions"] = good
    return data


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        "أهلاً! ابعتلي صفحة (صورة أو PDF أو نص) وراح أشرحها لك وأختبرك عليها 📚"
    )


async def on_content(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = update.message
    uid = m.from_user.id
    if not allowed(uid):
        return

    blocks = []
    if m.photo:
        f = await m.photo[-1].get_file()
        data = bytes(await f.download_as_bytearray())
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(data).decode(),
                },
            }
        )
    elif m.document and m.document.mime_type == "application/pdf":
        if (m.document.file_size or 0) > 19 * 1024 * 1024:
            await m.reply_text("الملف كبير، ابعته أصغر من 19MB.")
            return
        f = await m.document.get_file()
        data = bytes(await f.download_as_bytearray())
        blocks.append(
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.b64encode(data).decode(),
                },
            }
        )
    elif m.text:
        blocks.append({"type": "text", "text": m.text})
    else:
        await m.reply_text("ابعت صورة أو PDF أو نص.")
        return

    if m.caption:
        blocks.append({"type": "text", "text": m.caption})
    blocks.append({"type": "text", "text": "اشرح هذا المحتوى وأعطني الأسئلة."})

    wait = await m.reply_text("⏳ عم أقرأ الصفحة...")
    try:
        resp = await client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM,
            messages=[{"role": "user", "content": blocks}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        data = parse_json(text)
    except Exception:
        log.exception("generation failed")
        await wait.edit_text("صار خطأ، جرّب تبعت الصفحة مرة ثانية.")
        return

    await wait.delete()
    explanation = data.get("explanation", "")
    for i in range(0, len(explanation), 4000):
        await m.reply_text(explanation[i : i + 4000])

    if not data["questions"]:
        await m.reply_text("ما قدرت أطلع أسئلة من هالصفحة.")
        return

    s = {
        "questions": data["questions"],
        "order": list(range(len(data["questions"]))),
        "pos": 0,
        "score": 0,
        "wrong": [],
    }
    sessions[uid] = s
    await m.reply_text("جاهز تتختبر؟ يلا نبدأ 🎯")
    await send_question(m.chat_id, ctx, s)


async def send_question(chat_id: int, ctx: ContextTypes.DEFAULT_TYPE, s: dict):
    q = s["questions"][s["order"][s["pos"]]]
    text = f"سؤال {s['pos'] + 1}/{len(s['order'])}\n\n{q['q']}\n\n" + "\n".join(
        f"{LETTERS[i]}) {o}" for i, o in enumerate(q["options"])
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(LETTERS[i], callback_data=f"a:{s['pos']}:{i}")
                for i in range(len(q["options"]))
            ]
        ]
    )
    await ctx.bot.send_message(chat_id, text, reply_markup=kb)


async def on_answer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cq = update.callback_query
    await cq.answer()
    uid = cq.from_user.id
    s = sessions.get(uid)
    if not s:
        return
    _, pos, choice = cq.data.split(":")
    pos, choice = int(pos), int(choice)
    if pos != s["pos"]:
        return

    qi = s["order"][pos]
    q = s["questions"][qi]
    ok = choice == q["answer"]
    if ok:
        s["score"] += 1
    else:
        s["wrong"].append(qi)

    right = f"{LETTERS[q['answer']]}) {q['options'][q['answer']]}"
    result = "✅ صح!" if ok else f"❌ غلط. الجواب الصحيح: {right}"
    await cq.edit_message_text(f"{cq.message.text}\n\n{result}\n{q['why']}")

    s["pos"] += 1
    if s["pos"] < len(s["order"]):
        await send_question(cq.message.chat_id, ctx, s)
        return

    total = len(s["order"])
    msg = f"خلصنا! نتيجتك: {s['score']}/{total}"
    kb = None
    if s["wrong"]:
        msg += f"\nغلطت في {len(s['wrong'])} سؤال."
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔁 أعد الأسئلة الغلط", callback_data="retry")]]
        )
    else:
        msg += "\nممتاز! 🌟 ابعت صفحة جديدة لما تكون جاهز."
    await ctx.bot.send_message(cq.message.chat_id, msg, reply_markup=kb)


async def on_retry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cq = update.callback_query
    await cq.answer()
    s = sessions.get(cq.from_user.id)
    if not s or not s["wrong"]:
        return
    s["order"], s["wrong"], s["pos"], s["score"] = s["wrong"], [], 0, 0
    await cq.edit_message_reply_markup(None)
    await send_question(cq.message.chat_id, ctx, s)


async def health(_request):
    return web.Response(text="ok")


async def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CallbackQueryHandler(on_answer, pattern=r"^a:"))
    app.add_handler(CallbackQueryHandler(on_retry, pattern=r"^retry$"))
    app.add_handler(
        MessageHandler(
            (filters.TEXT & ~filters.COMMAND) | filters.PHOTO | filters.Document.PDF,
            on_content,
        )
    )

    web_app = web.Application()
    web_app.add_routes([web.get("/", health)])
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    log.info("Bot is running")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
