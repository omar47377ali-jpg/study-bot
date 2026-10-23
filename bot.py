import asyncio
import json
import logging
import os

from aiohttp import web
from google import genai
from google.genai import types
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
MODEL = os.getenv("MODEL", "gemini-flash-lite-latest")
NUM_Q = int(os.getenv("NUM_QUESTIONS", "8"))
PORT = int(os.getenv("PORT", "10000"))
ALLOWED = {int(x) for x in os.getenv("ALLOWED_IDS", "").split(",") if x.strip()}

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
sessions: dict[int, dict] = {}

LETTERS = {"en": ["A", "B", "C", "D"], "ar": ["أ", "ب", "ج", "د"]}

T = {
    "en": {
        "ready": "Ready for the quiz? Let's go 🎯",
        "q": "Question",
        "correct": "✅ Correct!",
        "wrong": "❌ Wrong. Correct answer: {r}",
        "done": "Done! Your score: {s}/{t}",
        "missed": "\nYou missed {n} question(s).",
        "retry": "🔁 Retry missed questions",
        "perfect": "\nPerfect! 🌟 Send a new page when you're ready.",
    },
    "ar": {
        "ready": "جاهز للاختبار؟ يلا نبدأ 🎯",
        "q": "سؤال",
        "correct": "✅ صح!",
        "wrong": "❌ غلط. الجواب الصحيح: {r}",
        "done": "خلصنا! نتيجتك: {s}/{t}",
        "missed": "\nغلطت في {n} سؤال.",
        "retry": "🔁 أعد الأسئلة الغلط",
        "perfect": "\nممتاز! 🌟 ابعت صفحة جديدة لما تكون جاهز.",
    },
}

SYSTEM = """You are an expert university tutor. The student sends you a page (image, PDF, or text).

STEP 0 - DETECT THE LANGUAGE:
Look at the main content of the page itself (ignore the student's captions or instructions). If the page is mainly Arabic, set "language" to "ar". Otherwise set "language" to "en".

IF language is "en" (English page):
- EXPLANATION: cover EVERYTHING on the page: every concept, definition, formula, term, example, table, and figure. Do not skip any detail. Organize into short numbered sections. In each section, first explain clearly in English, then explain the same idea in simple Arabic (Jordanian-friendly Modern Arabic), keeping technical terms in English.
- QUESTIONS: English only (question, options, and explanation).
- End the explanation with a list called "Key Takeaways / أهم النقاط".

IF language is "ar" (Arabic page):
- EXPLANATION: Arabic only, in clear simple Arabic. Cover EVERYTHING on the page: every concept, definition, formula, term, example, table, and figure. Do not skip any detail. Organize into short numbered sections. You may put the English technical term in parentheses on first mention.
- QUESTIONS: Arabic only (question, options, and explanation).
- End the explanation with a list called "أهم النقاط".

COMMON RULES:
- Add a short real-world example or intuition where it helps. Explain formulas step by step and say what each symbol means.
- Plain text only: no markdown symbols (no *, #, or backticks). Use numbering, line breaks, and a few emojis for headings.
- Write exactly NUM_Q multiple-choice questions covering ALL parts of the page, from easy to challenging, testing understanding and application, not just memorization.
- Each question has 4 options, exactly one correct. Vary the position of the correct answer.
- "why" explains clearly why the answer is correct and briefly why the common wrong choices are wrong.

Return ONLY valid JSON, no code fences:
{"language": "en", "explanation": "...", "questions": [{"q": "...", "options": ["...", "...", "...", "..."], "answer": 0, "why": "..."}]}
"answer" is the zero-based index of the correct option. Use \\n for line breaks inside JSON strings."""
SYSTEM = SYSTEM.replace("NUM_Q", str(NUM_Q))


def allowed(uid: int) -> bool:
    return not ALLOWED or uid in ALLOWED


def parse_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    data = json.loads(text[start : end + 1], strict=False)
    good = []
    for q in data.get("questions", []):
        opts = q.get("options", [])
        ans = q.get("answer")
        if q.get("q") and 2 <= len(opts) <= 4 and isinstance(ans, int) and 0 <= ans < len(opts):
            q.setdefault("why", "")
            good.append(q)
    data["questions"] = good
    data["language"] = "ar" if data.get("language") == "ar" else "en"
    return data


def chunks(text: str, n: int = 4000) -> list[str]:
    out = []
    while len(text) > n:
        cut = text.rfind("\n", 0, n)
        if cut < n // 2:
            cut = n
        out.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text.strip():
        out.append(text)
    return out


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        "أهلاً! ابعتلي صفحة (صورة أو PDF أو نص) وراح أشرحها لك وأختبرك عليها 📚\n\n"
        "Send me a page (photo, PDF, or text) and I'll explain it and quiz you."
    )


async def on_content(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = update.message
    uid = m.from_user.id
    if not allowed(uid):
        return

    parts = []
    if m.photo:
        f = await m.photo[-1].get_file()
        data = bytes(await f.download_as_bytearray())
        parts.append(types.Part.from_bytes(data=data, mime_type="image/jpeg"))
    elif m.document and m.document.mime_type == "application/pdf":
        if (m.document.file_size or 0) > 19 * 1024 * 1024:
            await m.reply_text("الملف كبير، ابعته أصغر من 19MB.")
            return
        f = await m.document.get_file()
        data = bytes(await f.download_as_bytearray())
        parts.append(types.Part.from_bytes(data=data, mime_type="application/pdf"))
    elif m.text:
        parts.append(types.Part.from_text(text=m.text))
    else:
        await m.reply_text("ابعت صورة أو PDF أو نص.")
        return

    if m.caption:
        parts.append(types.Part.from_text(text=m.caption))
    parts.append(
        types.Part.from_text(
            text="Detect the page language, explain this content comprehensively, and give me the questions."
        )
    )

    wait = await m.reply_text("⏳ عم أقرأ الصفحة... / Reading the page...")
    try:
        resp = await client.aio.models.generate_content(
            model=MODEL,
            contents=parts,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                response_mime_type="application/json",
                max_output_tokens=16000,
            ),
        )
        data = parse_json(resp.text)
    except Exception:
        log.exception("generation failed")
        await wait.edit_text("صار خطأ، جرّب تبعت الصفحة مرة ثانية. / Something went wrong, please try again.")
        return

    await wait.delete()
    lang = data["language"]
    for part in chunks(data.get("explanation", "")):
        await m.reply_text(part)

    if not data["questions"]:
        await m.reply_text("ما قدرت أطلع أسئلة من هالصفحة. / Couldn't generate questions.")
        return

    s = {
        "questions": data["questions"],
        "order": list(range(len(data["questions"]))),
        "pos": 0,
        "score": 0,
        "wrong": [],
        "lang": lang,
    }
    sessions[uid] = s
    await m.reply_text(T[lang]["ready"])
    await send_question(m.chat_id, ctx, s)


async def send_question(chat_id: int, ctx: ContextTypes.DEFAULT_TYPE, s: dict):
    lang = s["lang"]
    letters = LETTERS[lang]
    q = s["questions"][s["order"][s["pos"]]]
    text = f"{T[lang]['q']} {s['pos'] + 1}/{len(s['order'])}\n\n{q['q']}\n\n" + "\n".join(
        f"{letters[i]}) {o}" for i, o in enumerate(q["options"])
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(letters[i], callback_data=f"a:{s['pos']}:{i}")
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

    lang = s["lang"]
    t = T[lang]
    letters = LETTERS[lang]
    qi = s["order"][pos]
    q = s["questions"][qi]
    ok = choice == q["answer"]
    if ok:
        s["score"] += 1
    else:
        s["wrong"].append(qi)

    right = f"{letters[q['answer']]}) {q['options'][q['answer']]}"
    result = t["correct"] if ok else t["wrong"].format(r=right)
    await cq.edit_message_text(f"{cq.message.text}\n\n{result}\n{q['why']}")

    s["pos"] += 1
    if s["pos"] < len(s["order"]):
        await send_question(cq.message.chat_id, ctx, s)
        return

    total = len(s["order"])
    msg = t["done"].format(s=s["score"], t=total)
    kb = None
    if s["wrong"]:
        msg += t["missed"].format(n=len(s["wrong"]))
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(t["retry"], callback_data="retry")]]
        )
    else:
        msg += t["perfect"]
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
    await app.updater.start_polling(drop_pending_updates=False)
    log.info("Bot is running")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
