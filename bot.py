"""
Prompt Engine Telegram Bot
A professional prompt-crafting bot for Claude with full inline keyboard UI.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Optional

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    constants,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from prompt_generator import generate_prompt

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_IDS_RAW = os.getenv("ALLOWED_USER_IDS", "")
ALLOWED_IDS: set[int] = (
    {int(x.strip()) for x in ALLOWED_IDS_RAW.split(",") if x.strip()}
    if ALLOWED_IDS_RAW else set()
)

# ─── Conversation states ───────────────────────────────────────────────────────
AWAIT_TASK = 1
AWAIT_ROLE = 2
AWAIT_CONTEXT = 3
AWAIT_AVOID = 4

# ─── Data keys stored in user_data ────────────────────────────────────────────
KEY = "pe_state"


def _state(ctx: ContextTypes.DEFAULT_TYPE) -> dict:
    if KEY not in ctx.user_data:
        ctx.user_data[KEY] = {
            "mode": None,
            "task": "",
            "role": "",
            "context": "",
            "tone": None,
            "output_format": None,
            "length": None,
            "audience": None,
            "extras": [],
            "avoid": "",
        }
    return ctx.user_data[KEY]


def _reset(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx.user_data[KEY] = {
        "mode": None,
        "task": "",
        "role": "",
        "context": "",
        "tone": None,
        "output_format": None,
        "length": None,
        "audience": None,
        "extras": [],
        "avoid": "",
    }


def _allowed(update: Update) -> bool:
    if not ALLOWED_IDS:
        return True
    user = update.effective_user
    return user is not None and user.id in ALLOWED_IDS


# ─── Keyboards ────────────────────────────────────────────────────────────────

MODES = [
    ("✍️  Write", "write"),
    ("💻  Code", "code"),
    ("📊  Analyze", "analyze"),
    ("🐛  Debug", "debug"),
    ("📚  Learn", "learn"),
    ("💡  Brainstorm", "brainstorm"),
]

TONES = ["Professional", "Casual", "Academic", "Friendly", "Direct", "Creative", "Technical"]
FORMATS = ["Paragraphs", "Bullet Points", "Step-by-Step", "Table", "Code Block", "JSON", "Markdown"]
LENGTHS = ["Brief", "Medium", "Detailed", "Comprehensive", "As needed"]
AUDIENCES = ["Expert", "Intermediate", "Beginner", "Non-technical", "Mixed"]
EXTRAS = [
    "Include examples",
    "Suggest alternatives",
    "Think step-by-step",
    "Pros & cons",
    "No filler / no fluff",
    "Be critical / honest",
    "Actionable output",
    "Include code snippets",
]


def _mode_keyboard(selected: Optional[str] = None) -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(MODES), 3):
        row = []
        for label, val in MODES[i:i+3]:
            mark = "✅ " if val == selected else ""
            row.append(InlineKeyboardButton(f"{mark}{label}", callback_data=f"mode:{val}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _chip_keyboard(options: list[str], selected, prefix: str, cols: int = 2) -> InlineKeyboardMarkup:
    rows = []
    is_multi = isinstance(selected, list)
    for i in range(0, len(options), cols):
        row = []
        for opt in options[i:i+cols]:
            if is_multi:
                mark = "✅ " if opt in selected else ""
            else:
                mark = "✅ " if opt == selected else ""
            row.append(InlineKeyboardButton(f"{mark}{opt}", callback_data=f"{prefix}:{opt}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️  Back", callback_data="adv:back")])
    return InlineKeyboardMarkup(rows)


def _advanced_menu(s: dict) -> InlineKeyboardMarkup:
    def badge(val, is_list=False):
        if is_list:
            return f" ({len(val)})" if val else ""
        return f" · {val}" if val else ""

    rows = [
        [InlineKeyboardButton(f"🎨  Tone{badge(s['tone'])}", callback_data="adv:tone"),
         InlineKeyboardButton(f"📄  Format{badge(s['output_format'])}", callback_data="adv:format")],
        [InlineKeyboardButton(f"📏  Length{badge(s['length'])}", callback_data="adv:length"),
         InlineKeyboardButton(f"👥  Audience{badge(s['audience'])}", callback_data="adv:audience")],
        [InlineKeyboardButton(f"✨  Extras{badge(s['extras'], is_list=True)}", callback_data="adv:extras")],
        [InlineKeyboardButton(f"🚫  Avoid{badge(s['avoid'])}", callback_data="adv:avoid")],
        [InlineKeyboardButton("⬅️  Back to main", callback_data="adv:back_main")],
    ]
    return InlineKeyboardMarkup(rows)


def _main_menu(s: dict) -> InlineKeyboardMarkup:
    task_set = bool(s.get("task"))
    mode_set = bool(s.get("mode"))
    rows = [
        [InlineKeyboardButton("🎯  Change Mode", callback_data="main:mode")],
        [InlineKeyboardButton("✏️  Edit Task", callback_data="main:task")],
        [InlineKeyboardButton("👤  Role / Persona", callback_data="main:role"),
         InlineKeyboardButton("📝  Context", callback_data="main:context")],
        [InlineKeyboardButton("⚙️  Advanced Options", callback_data="main:advanced")],
    ]
    if task_set and mode_set:
        rows.append([InlineKeyboardButton("✨  Generate Prompt", callback_data="main:generate")])
    rows.append([InlineKeyboardButton("🔄  Start Over", callback_data="main:reset")])
    return InlineKeyboardMarkup(rows)


def _status_text(s: dict) -> str:
    mode_label = next((l for l, v in MODES if v == s["mode"]), None)
    lines = ["*⚡ Prompt Engine*", ""]
    lines.append(f"{'✅' if s['mode'] else '⬜'} *Mode:* {mode_label or '_(not set)_'}")
    lines.append(f"{'✅' if s['task'] else '⬜'} *Task:* {(s['task'][:60] + '…') if len(s['task']) > 60 else s['task'] or '_(not set)_'}")
    if s.get("role"):
        lines.append(f"👤 *Role:* {s['role'][:50]}")
    if s.get("tone") or s.get("output_format") or s.get("length") or s.get("audience") or s.get("extras"):
        adv_parts = []
        if s["tone"]: adv_parts.append(s["tone"])
        if s["output_format"]: adv_parts.append(s["output_format"])
        if s["length"]: adv_parts.append(s["length"])
        if s["audience"]: adv_parts.append(s["audience"])
        if s["extras"]: adv_parts.append(f"{len(s['extras'])} extras")
        lines.append(f"⚙️ *Advanced:* {', '.join(adv_parts)}")
    return "\n".join(lines)


# ─── Handlers ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    _reset(ctx)
    await update.message.reply_text(
        "👋 *Welcome to Prompt Engine Bot\\!*\n\n"
        "I help you craft perfect, professional prompts for Claude\\.\n\n"
        "First, choose what you want to do:",
        parse_mode=constants.ParseMode.MARKDOWN_V2,
        reply_markup=_mode_keyboard(),
    )


async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    _reset(ctx)
    await update.message.reply_text(
        "🔄 *New prompt* — choose a mode:",
        parse_mode=constants.ParseMode.MARKDOWN_V2,
        reply_markup=_mode_keyboard(),
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    s = _state(ctx)
    await update.message.reply_text(
        _status_text(s),
        parse_mode=constants.ParseMode.MARKDOWN_V2,
        reply_markup=_main_menu(s),
    )


async def cmd_generate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    s = _state(ctx)
    if not s["mode"] or not s["task"]:
        await update.message.reply_text(
            "⚠️ Please set a *mode* and *task* first\\.",
            parse_mode=constants.ParseMode.MARKDOWN_V2,
        )
        return
    await _do_generate(update.message.reply_text, s)


async def _do_generate(reply_fn, s: dict) -> None:
    prompt = generate_prompt(s)
    # Split if too long for one message
    max_len = 4000
    header = "✨ *Your Generated Prompt:*\n\n"
    if len(prompt) <= max_len - len(header):
        await reply_fn(
            f"{header}`{prompt}`",
            parse_mode=constants.ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋  Copy text above", callback_data="noop")],
                [InlineKeyboardButton("🔄  New Prompt", callback_data="main:reset"),
                 InlineKeyboardButton("✏️  Edit", callback_data="main:task")],
            ]),
        )
    else:
        await reply_fn("✨ *Your Generated Prompt:*", parse_mode=constants.ParseMode.MARKDOWN_V2)
        # Send in chunks
        for i in range(0, len(prompt), max_len):
            chunk = prompt[i:i+max_len]
            await reply_fn(f"`{chunk}`", parse_mode=constants.ParseMode.MARKDOWN_V2)
        await reply_fn(
            "👆 Prompt sent above\\. Copy it and paste into Claude\\!",
            parse_mode=constants.ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄  New Prompt", callback_data="main:reset"),
                InlineKeyboardButton("✏️  Edit", callback_data="main:task"),
            ]]),
        )


# ─── Callback query handler ────────────────────────────────────────────────────

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    if not _allowed(update):
        return None
    query = update.callback_query
    await query.answer()
    data = query.data
    s = _state(ctx)

    # ── Mode selection ──
    if data.startswith("mode:"):
        val = data.split(":", 1)[1]
        s["mode"] = val
        mode_label = next((l for l, v in MODES if v == val), val)
        await query.edit_message_text(
            f"*Mode set:* {mode_label}\n\nNow describe your task:",
            parse_mode=constants.ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️  Skip to Advanced Options", callback_data="main:advanced")],
                [InlineKeyboardButton("🔄  Change Mode", callback_data="main:mode")],
            ]),
        )
        ctx.user_data["_await"] = "task"
        return AWAIT_TASK

    # ── Main menu ──
    if data.startswith("main:"):
        action = data.split(":", 1)[1]

        if action == "mode":
            await query.edit_message_text(
                "Choose a mode:",
                reply_markup=_mode_keyboard(s["mode"]),
            )
            return None

        if action == "task":
            await query.edit_message_text(
                "✏️ *Describe your task:*\n\nType your task description in the chat:",
                parse_mode=constants.ParseMode.MARKDOWN_V2,
            )
            ctx.user_data["_await"] = "task"
            return AWAIT_TASK

        if action == "role":
            await query.edit_message_text(
                "👤 *Role / Persona* _(optional)_\n\n"
                "Example: _a senior QA engineer who has tested 200\\+ websites_\n\n"
                "Type your role or tap Skip:",
                parse_mode=constants.ParseMode.MARKDOWN_V2,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⏭️  Skip", callback_data="skip:role"),
                ]]),
            )
            ctx.user_data["_await"] = "role"
            return AWAIT_ROLE

        if action == "context":
            await query.edit_message_text(
                "📝 *Context* _(optional)_\n\n"
                "Background info: who you are, project details, constraints…\n\n"
                "Type your context or tap Skip:",
                parse_mode=constants.ParseMode.MARKDOWN_V2,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⏭️  Skip", callback_data="skip:context"),
                ]]),
            )
            ctx.user_data["_await"] = "context"
            return AWAIT_CONTEXT

        if action == "advanced":
            await query.edit_message_text(
                "⚙️ *Advanced Options*\n\nFine\\-tune your prompt:",
                parse_mode=constants.ParseMode.MARKDOWN_V2,
                reply_markup=_advanced_menu(s),
            )
            return None

        if action == "generate":
            if not s["mode"] or not s["task"]:
                await query.edit_message_text(
                    "⚠️ Please set a *mode* and describe your *task* first\\.",
                    parse_mode=constants.ParseMode.MARKDOWN_V2,
                    reply_markup=_main_menu(s),
                )
                return None
            await query.edit_message_text(
                _status_text(s),
                parse_mode=constants.ParseMode.MARKDOWN_V2,
            )
            await _do_generate(query.message.reply_text, s)
            return None

        if action == "reset":
            _reset(ctx)
            await query.edit_message_text(
                "🔄 *Starting fresh\\!* Choose a mode:",
                parse_mode=constants.ParseMode.MARKDOWN_V2,
                reply_markup=_mode_keyboard(),
            )
            return None

        if action == "status":
            await query.edit_message_text(
                _status_text(s),
                parse_mode=constants.ParseMode.MARKDOWN_V2,
                reply_markup=_main_menu(s),
            )
            return None

    # ── Advanced sub-menus ──
    if data.startswith("adv:"):
        action = data.split(":", 1)[1]

        if action == "back_main":
            await query.edit_message_text(
                _status_text(s),
                parse_mode=constants.ParseMode.MARKDOWN_V2,
                reply_markup=_main_menu(s),
            )
            return None

        if action == "back":
            await query.edit_message_text(
                "⚙️ *Advanced Options*\n\nFine\\-tune your prompt:",
                parse_mode=constants.ParseMode.MARKDOWN_V2,
                reply_markup=_advanced_menu(s),
            )
            return None

        if action == "tone":
            await query.edit_message_text(
                "🎨 *Tone* — pick one:",
                parse_mode=constants.ParseMode.MARKDOWN_V2,
                reply_markup=_chip_keyboard(TONES, s["tone"], "tone"),
            )
            return None

        if action == "format":
            await query.edit_message_text(
                "📄 *Output Format* — pick one:",
                parse_mode=constants.ParseMode.MARKDOWN_V2,
                reply_markup=_chip_keyboard(FORMATS, s["output_format"], "fmt", cols=2),
            )
            return None

        if action == "length":
            await query.edit_message_text(
                "📏 *Length* — pick one:",
                parse_mode=constants.ParseMode.MARKDOWN_V2,
                reply_markup=_chip_keyboard(LENGTHS, s["length"], "len"),
            )
            return None

        if action == "audience":
            await query.edit_message_text(
                "👥 *Audience* — pick one:",
                parse_mode=constants.ParseMode.MARKDOWN_V2,
                reply_markup=_chip_keyboard(AUDIENCES, s["audience"], "aud"),
            )
            return None

        if action == "extras":
            await query.edit_message_text(
                "✨ *Extra Instructions* — pick any:",
                parse_mode=constants.ParseMode.MARKDOWN_V2,
                reply_markup=_chip_keyboard(EXTRAS, s["extras"], "ext"),
            )
            return None

        if action == "avoid":
            await query.edit_message_text(
                "🚫 *Avoid* _(optional)_\n\nType what Claude should avoid \\(e\\.g\\. _jargon, bullet points_\\):\n\nOr tap Skip:",
                parse_mode=constants.ParseMode.MARKDOWN_V2,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⏭️  Skip", callback_data="skip:avoid"),
                    InlineKeyboardButton("🗑️  Clear", callback_data="clear:avoid"),
                ]]),
            )
            ctx.user_data["_await"] = "avoid"
            return AWAIT_AVOID

    # ── Chip selections ──
    for prefix, key, options, multi in [
        ("tone", "tone", TONES, False),
        ("fmt", "output_format", FORMATS, False),
        ("len", "length", LENGTHS, False),
        ("aud", "audience", AUDIENCES, False),
        ("ext", "extras", EXTRAS, True),
    ]:
        if data.startswith(f"{prefix}:"):
            val = data.split(":", 1)[1]
            if multi:
                lst = s[key]
                if val in lst:
                    lst.remove(val)
                else:
                    lst.append(val)
            else:
                s[key] = None if s[key] == val else val  # toggle

            # Re-render the same chip keyboard
            labels = {"tone": "🎨 *Tone*", "output_format": "📄 *Output Format*",
                      "length": "📏 *Length*", "audience": "👥 *Audience*", "extras": "✨ *Extra Instructions*"}
            raw_options = {"tone": TONES, "output_format": FORMATS, "length": LENGTHS,
                           "audience": AUDIENCES, "extras": EXTRAS}
            cols_map = {"tone": 2, "output_format": 2, "length": 2, "audience": 2, "extras": 2}

            await query.edit_message_text(
                f"{labels[key]} — {'pick any' if multi else 'pick one'}:",
                parse_mode=constants.ParseMode.MARKDOWN_V2,
                reply_markup=_chip_keyboard(raw_options[key], s[key], prefix, cols=cols_map[key]),
            )
            return None

    # ── Skip / Clear ──
    if data.startswith("skip:"):
        field = data.split(":", 1)[1]
        ctx.user_data.pop("_await", None)
        await query.edit_message_text(
            _status_text(s),
            parse_mode=constants.ParseMode.MARKDOWN_V2,
            reply_markup=_main_menu(s),
        )
        return ConversationHandler.END

    if data.startswith("clear:"):
        field = data.split(":", 1)[1]
        s[field] = "" if isinstance(s.get(field), str) else None
        await query.edit_message_text(
            f"🗑️ _{field.capitalize()} cleared\\._\n\n" + _status_text(s),
            parse_mode=constants.ParseMode.MARKDOWN_V2,
            reply_markup=_main_menu(s),
        )
        return None

    if data == "noop":
        return None

    return None


# ─── Text message handlers (conversation states) ───────────────────────────────

async def recv_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    if not _allowed(update):
        return None
    s = _state(ctx)
    field = ctx.user_data.get("_await")
    text = update.message.text.strip()

    if field == "task":
        s["task"] = text
        ctx.user_data.pop("_await", None)
        await update.message.reply_text(
            _status_text(s),
            parse_mode=constants.ParseMode.MARKDOWN_V2,
            reply_markup=_main_menu(s),
        )
        return ConversationHandler.END

    if field == "role":
        s["role"] = text
        ctx.user_data.pop("_await", None)
        await update.message.reply_text(
            _status_text(s),
            parse_mode=constants.ParseMode.MARKDOWN_V2,
            reply_markup=_main_menu(s),
        )
        return ConversationHandler.END

    if field == "context":
        s["context"] = text
        ctx.user_data.pop("_await", None)
        await update.message.reply_text(
            _status_text(s),
            parse_mode=constants.ParseMode.MARKDOWN_V2,
            reply_markup=_main_menu(s),
        )
        return ConversationHandler.END

    if field == "avoid":
        s["avoid"] = text
        ctx.user_data.pop("_await", None)
        await update.message.reply_text(
            _status_text(s),
            parse_mode=constants.ParseMode.MARKDOWN_V2,
            reply_markup=_main_menu(s),
        )
        return ConversationHandler.END

    # Not in a conversation — show status
    await update.message.reply_text(
        _status_text(s),
        parse_mode=constants.ParseMode.MARKDOWN_V2,
        reply_markup=_main_menu(s),
    )
    return None


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    await update.message.reply_text(
        "📖 *Prompt Engine Bot Commands*\n\n"
        "/start — Start fresh, choose mode\n"
        "/new — New prompt \\(same as start\\)\n"
        "/status — See current settings\n"
        "/generate — Generate prompt now\n"
        "/help — Show this message\n\n"
        "💡 *How to use:*\n"
        "1\\. Choose a *mode* \\(Write, Code, Analyze…\\)\n"
        "2\\. Type your *task* description\n"
        "3\\. Optionally set *Role*, *Context*, and *Advanced Options*\n"
        "4\\. Tap *Generate Prompt* ✨\n\n"
        "The generated prompt is ready to paste into Claude\\!",
        parse_mode=constants.ParseMode.MARKDOWN_V2,
    )


# ─── App setup ────────────────────────────────────────────────────────────────

def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    # Conversation handler for text inputs
    conv = ConversationHandler(
        entry_points=[],
        states={
            AWAIT_TASK: [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_text)],
            AWAIT_ROLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_text)],
            AWAIT_CONTEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_text)],
            AWAIT_AVOID: [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_text)],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("generate", cmd_generate))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, recv_text))

    log.info("Prompt Engine Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
