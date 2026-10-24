import asyncio
import html
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
    TypeHandler,
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
history: dict[int, list] = {}
busy: set[int] = set()

LETTERS = {"en": ["A", "B", "C", "D"], "ar": ["أ", "ب", "ج", "د"]}

LIMIT_MSG = "وصلنا للحد المجاني لجيمناي، جرّب بعد شوي ⏳\nFree limit reached, please try again in a bit."
ERR_MSG = "صار خطأ، جرّب مرة ثانية.\nSomething went wrong, please try again."

CREDIT = (
    "👨‍💻 تم تطوير البوت بواسطة @mbxll0\n"
    "🎓 صمّم البوت: عمر\n\n"
    "Developed by @mbxll0\n"
    "Designed by Omar"
)

T = {
    "en": {
        "menu": "What next? 👇",
        "b_solve": "✅ Solve the questions",
        "b_quiz": "🎯 Start quiz",
        "b_sum": "📋 Summary",
        "b_cards": "🃏 Flashcards",
        "b_ask": "❓ Ask about the page",
        "b_more": "➕ New harder questions",
        "b_hint": "💡 Hint",
        "ready": "Ready for the quiz? Let's go 🎯",
        "q": "Question",
        "correct": "✅ Correct!",
        "wrong": "❌ Wrong. Correct answer: {r}",
        "done": "Score: {s}/{t} ({p}%)",
        "stats": "🔥 Best streak: {b}   💡 Hints used: {h}",
        "missed": "You missed {n} question(s).",
        "retry": "🔁 Retry missed questions",
        "ask_prompt": "Just type your question and I'll answer ✍️\n(Send /new to start over with a new page.)",
        "cards_title": "🃏 Flashcards (tap the black bar to reveal the answer)",
        "math": "🧮 Math problems detected! I'll solve them step by step, then give you similar practice questions.",
        "nohint": "No hint for this one.",
        "nosession": "Please send the page again 📄",
        "noq": "I couldn't build questions from this page.",
        "cleared": "Cleared! Send me a new page 📄",
        "titles": [
            (90, "🏆 Genius!"),
            (70, "🥇 Excellent!"),
            (50, "🥈 Good job!"),
            (0, "🥉 Keep practicing, you'll get there!"),
        ],
    },
    "ar": {
        "menu": "شو بدك تعمل هسا؟ 👇",
        "b_solve": "✅ حل الأسئلة",
        "b_quiz": "🎯 ابدأ الاختبار",
        "b_sum": "📋 ملخص",
        "b_cards": "🃏 بطاقات مراجعة",
        "b_ask": "❓ اسأل عن الصفحة",
        "b_more": "➕ أسئلة جديدة أصعب",
        "b_hint": "💡 تلميح",
        "ready": "جاهز للاختبار؟ يلا نبدأ 🎯",
        "q": "سؤال",
        "correct": "✅ صح!",
        "wrong": "❌ غلط. الجواب الصحيح: {r}",
        "done": "نتيجتك: {s}/{t} ({p}%)",
        "stats": "🔥 أطول سلسلة: {b}   💡 تلميحات: {h}",
        "missed": "غلطت في {n} سؤال.",
        "retry": "🔁 أعد الأسئلة الغلط",
        "ask_prompt": "اكتب سؤالك وبجاوبك ✍️\n(ابعت /new لتبدأ بصفحة جديدة.)",
        "cards_title": "🃏 بطاقات مراجعة (اضغط على الشريط الأسود لتظهر الإجابة)",
        "math": "🧮 اكتشفت مسائل رياضية! رح أحلها خطوة بخطوة وبعدين أعطيك مسائل تدريب مشابهة.",
        "nohint": "ما في تلميح لهالسؤال.",
        "nosession": "ابعت الصفحة من جديد 📄",
        "noq": "ما قدرت أطلع أسئلة من هالصفحة.",
        "cleared": "تمام! ابعت صفحة جديدة 📄",
        "titles": [
            (90, "🏆 عبقري!"),
            (70, "🥇 ممتاز!"),
            (50, "🥈 جيد!"),
            (0, "🥉 كمّل تمرين وراح توصل!"),
        ],
    },
}

SYSTEM = """You are an expert university tutor with a friendly, motivating style. The student sends you a page (image, PDF, or text).

STEP 1 - DETECT:
- "language": "ar" if the page itself is mainly Arabic, otherwise "en" (ignore the student's captions).
- "kind": "math" if the page mainly contains problems or exercises to be solved (math, physics, chemistry calculations, engineering numerical problems, equations, word problems). Otherwise "theory".

IF kind is "theory":
- EXPLANATION: cover EVERYTHING on the page: every concept, definition, formula, term, example, table, and figure. Do not skip any detail. Organize into short numbered sections. Add a short real-world example or intuition where it helps. Explain formulas step by step and say what each symbol means. End with a list "Key Takeaways" (or "أهم النقاط" for Arabic).
- QUESTIONS: test understanding and application across ALL parts of the page, easy to challenging.

IF kind is "math":
- EXPLANATION: (1) a short "Method and formulas" section with the ideas and formulas needed. (2) Then solve EVERY problem on the page: restate it briefly, show a numbered step-by-step solution with all calculations and units, and end with a clear line "Answer: ..." (or "الجواب: ..." for Arabic).
- QUESTIONS: practice problems similar to the ones on the page but with different numbers or setup, mixed difficulty. Each option is a number or a short expression; exactly one is correct; the wrong options must be plausible (typical mistakes). "why" must show the complete worked solution step by step. Double-check every calculation so the marked correct option is truly correct.

LANGUAGE RULES:
- If language is "en": the explanation is in English, and after each section/problem add the same idea in simple Arabic (Jordanian-friendly Modern Arabic), keeping technical terms in English. Questions, options, hints and "why" are in English only.
- If language is "ar": everything is in Arabic only (put the English technical term in parentheses on first mention).

COMMON RULES:
- Write exactly NUM_Q multiple-choice questions. Each has 4 options, exactly one correct. Vary the position of the correct answer.
- "why" explains clearly why the answer is correct and briefly why the common wrong choices are wrong.
- "hint" is one short hint that nudges toward the answer without revealing it.
- Plain text only: no markdown symbols (no *, #, or backticks) and NEVER LaTeX or dollar signs. Write math in Unicode: x², √, π, ×, ÷, ≤, ≥, ∫, Σ, x₁, fractions like (a+b)/(2c). Use numbering, line breaks and a few emojis for headings.

Return ONLY valid JSON, no code fences:
{"language": "en", "kind": "theory", "explanation": "...", "questions": [{"q": "...", "options": ["...", "...", "...", "..."], "answer": 0, "why": "...", "hint": "..."}]}
"answer" is the zero-based index of the correct option. Use \\n for line breaks inside JSON strings."""
SYSTEM = SYSTEM.replace("NUM_Q", str(NUM_Q))

REQUEST_SYS = """You are an expert, careful tutor. The student sends a page (image, PDF, or text) together with an instruction. Do exactly what the instruction asks about the page: solve, explain, translate, summarize, check answers, and so on.

When the task is to solve:
- If the instruction names specific questions (for example "solve question 3"), solve ONLY those. Otherwise solve every question and problem on the page, in order.
- For each one: restate it in one short line, then show numbered step-by-step working with all calculations and units, double-check the arithmetic, and end with a clear line "Answer: ..." (or "الجواب: ..." in Arabic).
- For multiple-choice questions: state the correct option and explain why. For written or theory questions: give a complete model answer.
- If part of the page is unreadable or ambiguous, say clearly what you could not read and state your assumption instead of guessing silently.

Language: reply in the language of the student's instruction (Arabic instruction: Arabic answer, keeping technical terms in English; English instruction: English answer).
Plain text only: no markdown symbols (no *, #, or backticks) and NEVER LaTeX or dollar signs. Write math in Unicode: x², √, π, ×, ÷, ≤, ≥, ∫, Σ, x₁. Use numbering, line breaks and a few emojis for headings."""

QUESTIONS_SYS = """You write practice questions from the given page. Return ONLY valid JSON, no code fences:
{"questions": [{"q": "...", "options": ["...", "...", "...", "..."], "answer": 0, "why": "...", "hint": "..."}]}
Write exactly NUM_Q multiple-choice questions, 4 options each, exactly one correct, "answer" is the zero-based index, vary its position. "hint" nudges without revealing. For math pages: new numeric problems similar to the page with different numbers, "why" is the complete step-by-step solution, verify all arithmetic, wrong options are typical mistakes. For theory pages: test understanding and application. Plain text only, Unicode math (x², √, π, ×, ÷), NEVER LaTeX. Follow the language requested in the instruction. Use \\n for line breaks inside JSON strings."""
QUESTIONS_SYS = QUESTIONS_SYS.replace("NUM_Q", str(NUM_Q))

SUMMARY_SYS = """Write a compact cheat sheet of the given page: key ideas, definitions, formulas, solving patterns for math, and common mistakes. Plain text only: no markdown symbols (no *, #, backticks), NEVER LaTeX; use Unicode math. Use numbering, line breaks and a few emojis. Follow the language requested in the instruction."""

CARDS_SYS = """Create 10 flashcards from the given page for active recall. Return ONLY valid JSON, no code fences:
{"cards": [{"front": "...", "back": "..."}]}
Each front and back is ONE short line of plain text (no line breaks, no markdown, no LaTeX; use Unicode math). For math pages: front = a problem type or formula question, back = the method or formula. Follow the language requested in the instruction."""

ASK_SYS = """You are a friendly, knowledgeable tutor and assistant. The student asks you a question.
- If a page (image, PDF, or text) is provided, use it as context when the question relates to it. If the question is not about the page, just answer it from your general knowledge.
- If the student asks you to solve, answer, or check questions on the page (for example "solve question 2"), do exactly that: solve step by step and end with the final answer.
- If there is a previous conversation, use it to understand follow-up questions.
- Reply in the same language as the student's question (Arabic question: Arabic answer; English question: English answer).
- For math, physics, or any calculation: show clear step-by-step work, double-check the arithmetic, and state the final answer.
- Be clear, accurate, and complete but not wordy. If you are not sure about something, say so honestly instead of guessing.
- Plain text only: no markdown symbols (no *, #, or backticks) and NEVER LaTeX or dollar signs. Write math in Unicode: x², √, π, ×, ÷, ≤, ≥. Use numbering, line breaks, and a few emojis when helpful."""


def allowed(uid: int) -> bool:
    return not ALLOWED or uid in ALLOWED


_ids = [int(x) for x in os.getenv("ALLOWED_IDS", "").split(",") if x.strip()]
ADMIN_ID = int(os.getenv("ADMIN_ID") or (_ids[0] if _ids else 0))
seen: dict[int, dict] = {}


def who(u) -> str:
    name = u.full_name or "?"
    tag = f"@{u.username}" if u.username else "(no username)"
    return f"{name} {tag} | id: {u.id}"


async def track(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not u or not update.message:
        return
    rec = seen.get(u.id)
    if rec:
        rec["count"] += 1
        return
    seen[u.id] = {"info": who(u), "count": 1}
    if ADMIN_ID and u.id != ADMIN_ID:
        status = "✅ allowed" if allowed(u.id) else "⛔ blocked (not in ALLOWED_IDS)"
        try:
            await ctx.bot.send_message(ADMIN_ID, f"🆕 New user\n{who(u)}\n{status}")
        except Exception:
            log.exception("admin notify failed")


async def users_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_ID or update.effective_user.id != ADMIN_ID:
        return
    if not seen:
        await update.message.reply_text("No users yet since last restart.")
        return
    lines = [f"{i}) {r['info']} — {r['count']} msgs" for i, r in enumerate(seen.values(), 1)]
    await update.message.reply_text("\n".join(lines)[:4000])


def load_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1], strict=False)


def clean_questions(raw) -> list[dict]:
    good = []
    for q in raw or []:
        opts = q.get("options", [])
        ans = q.get("answer")
        if q.get("q") and 2 <= len(opts) <= 4 and isinstance(ans, int) and 0 <= ans < len(opts):
            q["options"] = [str(o) for o in opts]
            q["why"] = str(q.get("why", ""))
            q["hint"] = str(q.get("hint", ""))
            good.append(q)
    return good


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


def one_line(x) -> str:
    return " ".join(str(x).split())


def has_arabic(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06ff" for ch in text)


def text_note(lang: str) -> str:
    if lang == "ar":
        return "Write in Arabic only."
    return "Write in English, then add a short Arabic version of each part."


def solve_note(lang: str) -> str:
    if lang == "ar":
        return "Reply in Arabic (keep technical terms in English)."
    return "Reply in English, then add a brief Arabic summary of the final answers."


def quiz_note(lang: str) -> str:
    if lang == "ar":
        return "Questions, options, hints and explanations in Arabic only."
    return "Questions, options, hints and explanations in English only."


def new_session(parts, lang: str, kind: str, questions: list) -> dict:
    return {
        "parts": parts,
        "lang": lang,
        "kind": kind,
        "questions": questions,
        "order": [],
        "pos": 0,
        "score": 0,
        "wrong": [],
        "marks": [],
        "streak": 0,
        "best": 0,
        "hinted": set(),
    }


async def call_model(parts, instruction, system, as_json=True, max_tokens=16000) -> str:
    contents = list(parts) + [types.Part.from_text(text=instruction)]
    cfg = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json" if as_json else "text/plain",
        max_output_tokens=max_tokens,
    )
    last = None
    for attempt in range(3):
        try:
            resp = await client.aio.models.generate_content(
                model=MODEL, contents=contents, config=cfg
            )
            return resp.text or ""
        except Exception as e:
            last = e
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                raise
            await asyncio.sleep(2 * (attempt + 1))
    raise last


def fail_text(e: Exception) -> str:
    msg = str(e)
    return LIMIT_MSG if ("429" in msg or "RESOURCE_EXHAUSTED" in msg) else ERR_MSG


async def make_questions(s: dict, harder: bool = False) -> list[dict]:
    lang = s["lang"]
    prev = "\n".join(q["q"] for q in s["questions"])
    kind = s["kind"] if s["kind"] in ("math", "theory") else "detect from the page (math problems or theory)"
    flavor = "NEW and somewhat harder " if harder else ""
    instr = f"Write {NUM_Q} {flavor}questions. Content kind: {kind}. {quiz_note(lang)}"
    if prev:
        instr += f" Do not repeat these questions:\n{prev}"
    raw = await call_model(s["parts"], instr, QUESTIONS_SYS, True, 16000)
    return clean_questions(load_json(raw).get("questions"))


def menu_kb(lang: str) -> InlineKeyboardMarkup:
    t = T[lang]
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(t["b_solve"], callback_data="m:solve")],
            [InlineKeyboardButton(t["b_quiz"], callback_data="m:quiz")],
            [
                InlineKeyboardButton(t["b_sum"], callback_data="m:sum"),
                InlineKeyboardButton(t["b_cards"], callback_data="m:cards"),
            ],
            [
                InlineKeyboardButton(t["b_ask"], callback_data="m:ask"),
                InlineKeyboardButton(t["b_more"], callback_data="m:more"),
            ],
        ]
    )


async def send_menu(chat_id: int, ctx: ContextTypes.DEFAULT_TYPE, lang: str):
    await ctx.bot.send_message(chat_id, T[lang]["menu"], reply_markup=menu_kb(lang))


def bar(s: dict) -> str:
    total = len(s["order"])
    done = "".join("🟩" if ok else "🟥" for ok in s["marks"])
    return done + "⬜" * (total - len(s["marks"]))


def grade(lang: str, pct: int) -> str:
    for lim, txt in T[lang]["titles"]:
        if pct >= lim:
            return txt
    return ""


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        "أهلاً! 👋\n"
        "📄 ابعتلي صفحة (صورة أو PDF أو نص طويل) أو مسائل رياضيات، وراح أشرحها وأحلها وأختبرك عليها.\n"
        "✍️ اكتب تعليق مع الصورة (مثلاً: حل السؤال 2) وبنفذ طلبك بالضبط.\n"
        "💬 أو اكتب أي سؤال وبجاوبك مباشرة.\n\n"
        "Send me a page (photo, PDF, long text) or math problems and I'll explain, solve, and quiz you.\n"
        "Add a caption to the photo (e.g. \"solve question 2\") and I'll do exactly that.\n"
        "Or just type any question and I'll answer it.\n\n"
        "/new = new page   /about = credits\n\n"
        + CREDIT
    )


async def about_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update.effective_user.id):
        return
    await update.message.reply_text(CREDIT)


async def new_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not allowed(uid):
        return
    s = sessions.pop(uid, None)
    history.pop(uid, None)
    lang = s["lang"] if s else "ar"
    await update.message.reply_text(T[lang]["cleared"])


async def answer_question(m, ctx: ContextTypes.DEFAULT_TYPE):
    uid = m.from_user.id
    if uid in busy:
        await m.reply_text("⏳ ...")
        return
    busy.add(uid)
    try:
        await ctx.bot.send_chat_action(m.chat_id, "typing")
        s = sessions.get(uid)
        parts = list(s["parts"]) if s else []
        hist = history.get(uid, [])
        convo = ""
        if hist:
            convo = (
                "Previous conversation:\n"
                + "\n".join(f"Student: {q}\nTutor: {a[:800]}" for q, a in hist[-4:])
                + "\n\n"
            )
        text = await call_model(
            parts, f"{convo}Student question: {m.text}", ASK_SYS, False, 8000
        )
        text = text.strip() or "..."
        hist.append((m.text, text))
        history[uid] = hist[-6:]
        for part in chunks(text):
            await m.reply_text(part)
    except Exception as e:
        log.exception("ask failed")
        await m.reply_text(fail_text(e))
    finally:
        busy.discard(uid)


async def handle_request(m, ctx: ContextTypes.DEFAULT_TYPE, parts: list, caption: str):
    uid = m.from_user.id
    lang = "ar" if has_arabic(caption) else "en"
    wait = await m.reply_text("⏳ عم أشتغل على طلبك... / Working on it...")
    try:
        await ctx.bot.send_chat_action(m.chat_id, "typing")
        text = await call_model(
            parts, f"Student instruction: {caption}", REQUEST_SYS, False, 16000
        )
        text = text.strip() or "..."
    except Exception as e:
        log.exception("request failed")
        await wait.edit_text(fail_text(e))
        busy.discard(uid)
        return

    try:
        await wait.delete()
        for part in chunks(text):
            await m.reply_text(part)
        history.pop(uid, None)
        sessions[uid] = new_session(parts, lang, "auto", [])
        await send_menu(m.chat_id, ctx, lang)
    finally:
        busy.discard(uid)


async def on_content(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = update.message
    uid = m.from_user.id
    if not allowed(uid):
        return

    # Short text = a question. Long text = a new page to study.
    if m.text:
        limit = 600 if uid in sessions else 300
        if len(m.text) <= limit:
            await answer_question(m, ctx)
            return

    if uid in busy:
        await m.reply_text("⏳ ...")
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

    caption = (m.caption or "").strip()
    busy.add(uid)

    # A caption is an instruction: do exactly what it says (e.g. "solve question 3").
    if caption:
        await handle_request(m, ctx, parts, caption)
        return

    wait = await m.reply_text("⏳ عم أقرأ الصفحة... / Reading the page...")
    try:
        await ctx.bot.send_chat_action(m.chat_id, "typing")
        text = await call_model(
            parts,
            "Detect the page language and kind, explain/solve it comprehensively, and give me the questions.",
            SYSTEM,
            True,
            16000,
        )
        data = load_json(text)
        questions = clean_questions(data.get("questions"))
        lang = "ar" if data.get("language") == "ar" else "en"
        kind = "math" if data.get("kind") == "math" else "theory"
        explanation = str(data.get("explanation", ""))
    except Exception as e:
        log.exception("generation failed")
        await wait.edit_text(fail_text(e))
        busy.discard(uid)
        return

    try:
        await wait.delete()
        if kind == "math":
            await m.reply_text(T[lang]["math"])
        for part in chunks(explanation):
            await m.reply_text(part)

        history.pop(uid, None)
        sessions[uid] = new_session(parts, lang, kind, questions)
        await send_menu(m.chat_id, ctx, lang)
    finally:
        busy.discard(uid)


async def start_quiz(chat_id: int, ctx: ContextTypes.DEFAULT_TYPE, s: dict):
    lang = s["lang"]
    if not s["questions"]:
        await ctx.bot.send_message(chat_id, T[lang]["noq"])
        return
    s["order"] = list(range(len(s["questions"])))
    s["pos"], s["score"], s["wrong"] = 0, 0, []
    s["marks"], s["streak"], s["best"], s["hinted"] = [], 0, 0, set()
    await ctx.bot.send_message(chat_id, T[lang]["ready"])
    await send_question(chat_id, ctx, s)


async def send_question(chat_id: int, ctx: ContextTypes.DEFAULT_TYPE, s: dict):
    lang = s["lang"]
    t = T[lang]
    letters = LETTERS[lang]
    q = s["questions"][s["order"][s["pos"]]]
    text = (
        f"{bar(s)}\n\n{t['q']} {s['pos'] + 1}/{len(s['order'])}\n\n{q['q']}\n\n"
        + "\n".join(f"{letters[i]}) {o}" for i, o in enumerate(q["options"]))
    )
    rows = [
        [
            InlineKeyboardButton(letters[i], callback_data=f"a:{s['pos']}:{i}")
            for i in range(len(q["options"]))
        ],
        [InlineKeyboardButton(t["b_hint"], callback_data=f"h:{s['pos']}")],
    ]
    await ctx.bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup(rows))


async def on_hint(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cq = update.callback_query
    s = sessions.get(cq.from_user.id)
    if not s or not s["order"]:
        await cq.answer()
        return
    pos = int(cq.data.split(":")[1])
    if pos != s["pos"]:
        await cq.answer()
        return
    q = s["questions"][s["order"][pos]]
    hint = q.get("hint", "").strip()
    if not hint:
        await cq.answer(T[s["lang"]]["nohint"], show_alert=True)
        return
    s["hinted"].add(pos)
    await cq.answer(hint[:190], show_alert=True)


async def on_answer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cq = update.callback_query
    await cq.answer()
    uid = cq.from_user.id
    s = sessions.get(uid)
    if not s or not s["order"]:
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
    s["marks"].append(ok)
    if ok:
        s["score"] += 1
        s["streak"] += 1
        s["best"] = max(s["best"], s["streak"])
    else:
        s["wrong"].append(qi)
        s["streak"] = 0

    right = f"{letters[q['answer']]}) {q['options'][q['answer']]}"
    result = t["correct"] if ok else t["wrong"].format(r=right)
    fire = f"  🔥×{s['streak']}" if s["streak"] >= 3 else ""
    new_text = f"{cq.message.text}\n\n{result}{fire}\n\n{q['why']}"
    try:
        await cq.edit_message_text(new_text[:4090])
    except Exception:
        await ctx.bot.send_message(cq.message.chat_id, f"{result}{fire}\n\n{q['why']}"[:4090])

    s["pos"] += 1
    if s["pos"] < len(s["order"]):
        await send_question(cq.message.chat_id, ctx, s)
        return

    total = len(s["order"])
    pct = s["score"] * 100 // total
    msg = (
        f"{bar(s)}\n\n{t['done'].format(s=s['score'], t=total, p=pct)}\n"
        f"{grade(lang, pct)}\n"
        f"{t['stats'].format(b=s['best'], h=len(s['hinted']))}"
    )
    kb = None
    if s["wrong"]:
        msg += "\n" + t["missed"].format(n=len(s["wrong"]))
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(t["retry"], callback_data="retry")]]
        )
    await ctx.bot.send_message(cq.message.chat_id, msg, reply_markup=kb)
    await send_menu(cq.message.chat_id, ctx, lang)


async def on_retry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cq = update.callback_query
    await cq.answer()
    s = sessions.get(cq.from_user.id)
    if not s or not s["wrong"]:
        return
    s["order"], s["wrong"], s["pos"], s["score"] = s["wrong"], [], 0, 0
    s["marks"], s["streak"], s["best"], s["hinted"] = [], 0, 0, set()
    await cq.edit_message_reply_markup(None)
    await send_question(cq.message.chat_id, ctx, s)


async def on_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cq = update.callback_query
    await cq.answer()
    uid = cq.from_user.id
    if not allowed(uid):
        return
    chat = cq.message.chat_id
    s = sessions.get(uid)
    if not s:
        await ctx.bot.send_message(chat, T["ar"]["nosession"] + " / " + T["en"]["nosession"])
        return

    action = cq.data.split(":")[1]
    lang = s["lang"]
    t = T[lang]

    if action == "ask":
        await ctx.bot.send_message(chat, t["ask_prompt"])
        return
    if action == "quiz" and s["questions"]:
        await start_quiz(chat, ctx, s)
        return

    if uid in busy:
        return
    busy.add(uid)
    try:
        await ctx.bot.send_chat_action(chat, "typing")
        if action == "solve":
            text = await call_model(
                s["parts"],
                f"Solve every question and problem on the page now. {solve_note(lang)}",
                REQUEST_SYS,
                False,
                16000,
            )
            for part in chunks(text.strip() or "..."):
                await ctx.bot.send_message(chat, part)
            await send_menu(chat, ctx, lang)

        elif action == "sum":
            text = await call_model(
                s["parts"],
                f"Write the cheat sheet now. {text_note(lang)}",
                SUMMARY_SYS,
                False,
                6000,
            )
            for part in chunks(text or "..."):
                await ctx.bot.send_message(chat, part)
            await send_menu(chat, ctx, lang)

        elif action == "cards":
            raw = await call_model(
                s["parts"],
                f"Create the flashcards now. Language: {'Arabic' if lang == 'ar' else 'English'}.",
                CARDS_SYS,
                True,
                6000,
            )
            cards = load_json(raw).get("cards", [])
            lines = []
            for i, c in enumerate(cards, 1):
                front = html.escape(one_line(c.get("front", "")))
                back = html.escape(one_line(c.get("back", "")))
                lines.append(f"{i}) {front}\n<tg-spoiler>{back}</tg-spoiler>")
            body = t["cards_title"] + "\n\n" + "\n\n".join(lines)
            for part in chunks(body):
                await ctx.bot.send_message(chat, part, parse_mode="HTML")
            await send_menu(chat, ctx, lang)

        elif action in ("quiz", "more"):
            new_q = await make_questions(s, harder=(action == "more"))
            if not new_q:
                await ctx.bot.send_message(chat, t["noq"])
            else:
                s["questions"] = new_q
                await start_quiz(chat, ctx, s)
    except Exception as e:
        log.exception("menu action failed")
        await ctx.bot.send_message(chat, fail_text(e))
    finally:
        busy.discard(uid)


async def health(_request):
    return web.Response(text="ok")


async def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("new", new_cmd))
    app.add_handler(CommandHandler("about", about_cmd))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(TypeHandler(Update, track), group=-1)
    app.add_handler(CallbackQueryHandler(on_menu, pattern=r"^m:"))
    app.add_handler(CallbackQueryHandler(on_answer, pattern=r"^a:"))
    app.add_handler(CallbackQueryHandler(on_hint, pattern=r"^h:"))
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
