"""
Prompt Engine Telegram Bot — v2
Professional prompt-crafting bot for Claude with full inline keyboard UI.

Fixes vs v1:
- HTML parse mode everywhere (no MarkdownV2 escaping crashes on user content)
- Global error handler with user feedback
- Persistence via PicklePersistence (state survives restarts)
- Typing action while generating
- /cancel command to escape awaiting states
- Bot commands registered for autocomplete
- Callback queries always answered (no infinite spinner)
- Generated prompt sent as plain text (no backtick formatting crash)
- Dead ConversationHandler removed (clean _await pattern)
- Inline "Copy-ready" block using <pre> HTML tag
"""
from __future__ import annotations

import html
import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from telegram import (
    BotCommand,
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
    MessageHandler,
    PicklePersistence,
    filters,
)

from prompt_generator import generate_prompt

load_dotenv()

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_IDS_RAW = os.getenv("ALLOWED_USER_IDS", "")
ALLOWED_IDS: set[int] = (
    {int(x.strip()) for x in ALLOWED_IDS_RAW.split(",") if x.strip()}
    if ALLOWED_IDS_RAW else set()
)
PERSISTENCE_FILE = Path(os.getenv("PERSISTENCE_FILE", "data/bot_persistence"))

# ─── HTML helpers ─────────────────────────────────────────────────────────────
# Fix #1: Use HTML mode everywhere — only need to escape <, >, &
# Far safer than MarkdownV2 for user-supplied content.

def e(text: str) -> str:
    """Escape user content for safe HTML embedding."""
    return html.escape(str(text))


def bold(text: str) -> str:
    return f"<b>{e(text)}</b>"


def italic(text: str) -> str:
    return f"<i>{e(text)}</i>"


def code(text: str) -> str:
    return f"<code>{e(text)}</code>"


def pre(text: str) -> str:
    """Pre-formatted block — safe for any content including backticks."""
    return f"<pre>{e(text)}</pre>"


H = constants.ParseMode.HTML

# ─── State management ─────────────────────────────────────────────────────────
KEY = "pe_state"
AWAIT_KEY = "pe_await"

EMPTY_STATE = {
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


def _state(ctx: ContextTypes.DEFAULT_TYPE) -> dict:
    if KEY not in ctx.user_data:
        ctx.user_data[KEY] = dict(EMPTY_STATE)
    return ctx.user_data[KEY]


def _reset(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx.user_data[KEY] = dict(EMPTY_STATE)
    ctx.user_data.pop(AWAIT_KEY, None)


def _set_await(ctx: ContextTypes.DEFAULT_TYPE, field: Optional[str]) -> None:
    if field is None:
        ctx.user_data.pop(AWAIT_KEY, None)
    else:
        ctx.user_data[AWAIT_KEY] = field


def _get_await(ctx: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    return ctx.user_data.get(AWAIT_KEY)


# ─── Auth ─────────────────────────────────────────────────────────────────────
def _allowed(update: Update) -> bool:
    if not ALLOWED_IDS:
        return True
    user = update.effective_user
    return user is not None and user.id in ALLOWED_IDS


# ─── Static data ──────────────────────────────────────────────────────────────
MODES = [
    ("✍️ Write", "write"),
    ("💻 Code", "code"),
    ("📊 Analyze", "analyze"),
    ("🐛 Debug", "debug"),
    ("📚 Learn", "learn"),
    ("💡 Brainstorm", "brainstorm"),
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

CHIP_META = {
    # prefix → (state_key, options, is_multi, label, cols)
    "tone": ("tone",          TONES,     False, "🎨 Tone",              2),
    "fmt":  ("output_format", FORMATS,   False, "📄 Output Format",     2),
    "len":  ("length",        LENGTHS,   False, "📏 Length",            2),
    "aud":  ("audience",      AUDIENCES, False, "👥 Audience",          2),
    "ext":  ("extras",        EXTRAS,    True,  "✨ Extra Instructions", 2),
}

# ─── Keyboards ────────────────────────────────────────────────────────────────

def _mode_keyboard(selected: Optional[str] = None) -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(MODES), 3):
        row = []
        for label, val in MODES[i:i + 3]:
            mark = "✅ " if val == selected else ""
            row.append(InlineKeyboardButton(f"{mark}{label}", callback_data=f"mode:{val}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _chip_keyboard(options: list[str], selected, prefix: str, cols: int = 2) -> InlineKeyboardMarkup:
    rows = []
    is_multi = isinstance(selected, list)
    for i in range(0, len(options), cols):
        row = []
        for opt in options[i:i + cols]:
            active = (opt in selected) if is_multi else (opt == selected)
            mark = "✅ " if active else "  "
            row.append(InlineKeyboardButton(f"{mark}{opt}", callback_data=f"{prefix}:{opt}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Back to Advanced", callback_data="adv:back")])
    return InlineKeyboardMarkup(rows)


def _advanced_menu(s: dict) -> InlineKeyboardMarkup:
    def badge(val, is_list=False) -> str:
        if is_list:
            return f" ({len(val)})" if val else ""
        return f" · {val}" if val else ""

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🎨 Tone{badge(s['tone'])}", callback_data="adv:tone"),
            InlineKeyboardButton(f"📄 Format{badge(s['output_format'])}", callback_data="adv:format"),
        ],
        [
            InlineKeyboardButton(f"📏 Length{badge(s['length'])}", callback_data="adv:length"),
            InlineKeyboardButton(f"👥 Audience{badge(s['audience'])}", callback_data="adv:audience"),
        ],
        [InlineKeyboardButton(f"✨ Extras{badge(s['extras'], is_list=True)}", callback_data="adv:extras")],
        [InlineKeyboardButton(f"🚫 Avoid{badge(s['avoid'])}", callback_data="adv:avoid")],
        [InlineKeyboardButton("⬅️ Back to Main", callback_data="adv:back_main")],
    ])


def _main_menu(s: dict) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🎯 Change Mode", callback_data="main:mode")],
        [
            InlineKeyboardButton("✏️ Edit Task", callback_data="main:task"),
            InlineKeyboardButton("👤 Role", callback_data="main:role"),
        ],
        [
            InlineKeyboardButton("📝 Context", callback_data="main:context"),
            InlineKeyboardButton("⚙️ Advanced", callback_data="main:advanced"),
        ],
    ]
    if s.get("mode") and s.get("task"):
        rows.append([InlineKeyboardButton("✨ Generate Prompt", callback_data="main:generate")])
    rows.append([InlineKeyboardButton("🔄 Start Over", callback_data="main:reset")])
    return InlineKeyboardMarkup(rows)


def _after_generate_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 New Prompt", callback_data="main:reset"),
            InlineKeyboardButton("✏️ Edit Task", callback_data="main:task"),
        ],
        [InlineKeyboardButton("⚙️ Tweak Settings", callback_data="main:advanced")],
    ])


# ─── Status card ──────────────────────────────────────────────────────────────

def _status_text(s: dict) -> str:
    """Build a safe HTML status card — user content is always escaped."""
    mode_label = next((lbl for lbl, val in MODES if val == s["mode"]), None)

    task_preview = e(s["task"][:70] + "…") if len(s["task"]) > 70 else e(s["task"])

    lines = [f"<b>⚡ Prompt Engine</b>\n"]
    lines.append(f"{'✅' if s['mode'] else '⬜'} <b>Mode:</b> {e(mode_label) if mode_label else '<i>not set</i>'}")
    lines.append(f"{'✅' if s['task'] else '⬜'} <b>Task:</b> {task_preview or '<i>not set</i>'}")

    if s.get("role"):
        lines.append(f"👤 <b>Role:</b> {e(s['role'][:60])}")
    if s.get("context"):
        lines.append(f"📝 <b>Context:</b> {e(s['context'][:60])}…")

    adv = []
    if s["tone"]: adv.append(e(s["tone"]))
    if s["output_format"]: adv.append(e(s["output_format"]))
    if s["length"]: adv.append(e(s["length"]))
    if s["audience"]: adv.append(e(s["audience"]))
    if s["extras"]: adv.append(f"{len(s['extras'])} extras")
    if adv:
        lines.append(f"⚙️ <b>Advanced:</b> {', '.join(adv)}")

    if not s.get("mode") or not s.get("task"):
        lines.append("\n<i>💡 Set a mode and describe your task to generate a prompt.</i>")

    return "\n".join(lines)


# ─── Generate logic ───────────────────────────────────────────────────────────

async def _do_generate(ctx: ContextTypes.DEFAULT_TYPE, reply_fn, s: dict) -> None:
    """Fix #7: Send typing action, then generate and send prompt safely."""
    try:
        await ctx.bot.send_chat_action(
            chat_id=ctx._chat_id if hasattr(ctx, '_chat_id') else None,
            action=constants.ChatAction.TYPING,
        )
    except Exception:
        pass  # Not critical

    prompt = generate_prompt(s)

    header = "✨ <b>Your Generated Prompt:</b>\n\n"
    # Fix #3: Use <pre> tag — safe for ALL content including backticks
    body = pre(prompt)
    full = header + body

    MAX = 4096
    if len(full) <= MAX:
        await reply_fn(
            full,
            parse_mode=H,
            reply_markup=_after_generate_menu(),
        )
    else:
        # Send header separately, then prompt in chunks as plain pre blocks
        await reply_fn(f"✨ <b>Your Generated Prompt:</b>", parse_mode=H)
        for i in range(0, len(prompt), MAX - 20):
            chunk = prompt[i:i + MAX - 20]
            await reply_fn(pre(chunk), parse_mode=H)
        await reply_fn(
            "👆 <i>Prompt complete. Copy and paste into Claude!</i>",
            parse_mode=H,
            reply_markup=_after_generate_menu(),
        )


# ─── Command handlers ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        await update.message.reply_text("⛔ Access denied.")
        return
    _reset(ctx)
    await update.message.reply_text(
        "👋 <b>Welcome to Prompt Engine Bot!</b>\n\n"
        "I craft perfect, professional prompts for Claude.\n\n"
        "First — choose what you want to do:",
        parse_mode=H,
        reply_markup=_mode_keyboard(),
    )


async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    _reset(ctx)
    await update.message.reply_text(
        "🔄 <b>New prompt</b> — choose a mode:",
        parse_mode=H,
        reply_markup=_mode_keyboard(),
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    s = _state(ctx)
    await update.message.reply_text(
        _status_text(s),
        parse_mode=H,
        reply_markup=_main_menu(s),
    )


async def cmd_generate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    s = _state(ctx)
    if not s["mode"] or not s["task"]:
        await update.message.reply_text(
            "⚠️ Please set a <b>mode</b> and <b>task</b> first.",
            parse_mode=H,
            reply_markup=_main_menu(s),
        )
        return
    # Fix #7: Pass chat_id for typing action
    ctx._chat_id = update.effective_chat.id
    await _do_generate(ctx, update.message.reply_text, s)


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Fix #6: Allow user to escape any awaiting state."""
    if not _allowed(update):
        return
    _set_await(ctx, None)
    s = _state(ctx)
    await update.message.reply_text(
        "❌ <b>Cancelled.</b>",
        parse_mode=H,
        reply_markup=_main_menu(s),
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    await update.message.reply_text(
        "📖 <b>Prompt Engine Bot</b>\n\n"
        "<b>Commands:</b>\n"
        "/start — Start fresh\n"
        "/new — New prompt\n"
        "/status — Current settings\n"
        "/generate — Generate now\n"
        "/cancel — Cancel current input\n"
        "/help — This message\n\n"
        "<b>How to use:</b>\n"
        "1. Choose a <b>mode</b> (Write, Code, Analyze…)\n"
        "2. Type your <b>task</b> description\n"
        "3. Optionally set Role, Context, Advanced Options\n"
        "4. Tap <b>✨ Generate Prompt</b>\n\n"
        "<i>The prompt is ready to paste directly into Claude!</i>",
        parse_mode=H,
    )


# ─── Callback query router ────────────────────────────────────────────────────

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    # Fix #5: ALWAYS answer the callback query first — prevents infinite spinner
    await query.answer()

    if not _allowed(update):
        await query.answer("⛔ Access denied.", show_alert=True)
        return

    data = query.data
    s = _state(ctx)

    # ── Mode selection ──────────────────────────────────────────────────────
    if data.startswith("mode:"):
        val = data.split(":", 1)[1]
        s["mode"] = val
        mode_label = next((lbl for lbl, v in MODES if v == val), val)
        _set_await(ctx, "task")
        await query.edit_message_text(
            f"✅ <b>Mode:</b> {e(mode_label)}\n\n"
            f"Now <b>describe your task</b> — type it in the chat:\n"
            f"<i>(or use /cancel to go back)</i>",
            parse_mode=H,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭️ Skip — go to menu", callback_data="skip:task_skip")],
            ]),
        )
        return

    # ── Main menu actions ───────────────────────────────────────────────────
    if data.startswith("main:"):
        action = data.split(":", 1)[1]

        if action == "mode":
            await query.edit_message_text(
                "🎯 <b>Choose a mode:</b>",
                parse_mode=H,
                reply_markup=_mode_keyboard(s["mode"]),
            )

        elif action == "task":
            _set_await(ctx, "task")
            current = f"\n\n<i>Current: {e(s['task'][:60])}</i>" if s["task"] else ""
            await query.edit_message_text(
                f"✏️ <b>Describe your task:</b>{current}\n\nType in the chat:\n<i>(/cancel to abort)</i>",
                parse_mode=H,
            )

        elif action == "role":
            _set_await(ctx, "role")
            current = f"\n\n<i>Current: {e(s['role'])}</i>" if s["role"] else ""
            await query.edit_message_text(
                f"👤 <b>Role / Persona</b> <i>(optional)</i>{current}\n\n"
                f"Example: <i>a senior QA engineer who tested 200+ websites</i>\n\n"
                f"Type it or tap Skip:",
                parse_mode=H,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⏭️ Skip", callback_data="skip:role"),
                    InlineKeyboardButton("🗑️ Clear", callback_data="clear:role"),
                ]]),
            )

        elif action == "context":
            _set_await(ctx, "context")
            await query.edit_message_text(
                "📝 <b>Context</b> <i>(optional)</i>\n\n"
                "Background info: who you are, project details, constraints…\n\nType or Skip:",
                parse_mode=H,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⏭️ Skip", callback_data="skip:context"),
                    InlineKeyboardButton("🗑️ Clear", callback_data="clear:context"),
                ]]),
            )

        elif action == "advanced":
            await query.edit_message_text(
                "⚙️ <b>Advanced Options</b>\n\nFine-tune your prompt:",
                parse_mode=H,
                reply_markup=_advanced_menu(s),
            )

        elif action == "generate":
            if not s["mode"] or not s["task"]:
                await query.edit_message_text(
                    "⚠️ Please set a <b>mode</b> and <b>task</b> first.",
                    parse_mode=H,
                    reply_markup=_main_menu(s),
                )
                return
            await query.edit_message_text(_status_text(s), parse_mode=H)
            ctx._chat_id = query.message.chat_id
            await _do_generate(ctx, query.message.reply_text, s)

        elif action == "reset":
            _reset(ctx)
            await query.edit_message_text(
                "🔄 <b>Starting fresh!</b>\n\nChoose a mode:",
                parse_mode=H,
                reply_markup=_mode_keyboard(),
            )

        return

    # ── Advanced sub-menus ──────────────────────────────────────────────────
    if data.startswith("adv:"):
        action = data.split(":", 1)[1]

        if action == "back_main":
            await query.edit_message_text(
                _status_text(s), parse_mode=H, reply_markup=_main_menu(s)
            )
        elif action == "back":
            await query.edit_message_text(
                "⚙️ <b>Advanced Options</b>\n\nFine-tune your prompt:",
                parse_mode=H,
                reply_markup=_advanced_menu(s),
            )
        elif action == "tone":
            await query.edit_message_text(
                "🎨 <b>Tone</b> — pick one:", parse_mode=H,
                reply_markup=_chip_keyboard(TONES, s["tone"], "tone"),
            )
        elif action == "format":
            await query.edit_message_text(
                "📄 <b>Output Format</b> — pick one:", parse_mode=H,
                reply_markup=_chip_keyboard(FORMATS, s["output_format"], "fmt"),
            )
        elif action == "length":
            await query.edit_message_text(
                "📏 <b>Length</b> — pick one:", parse_mode=H,
                reply_markup=_chip_keyboard(LENGTHS, s["length"], "len"),
            )
        elif action == "audience":
            await query.edit_message_text(
                "👥 <b>Audience</b> — pick one:", parse_mode=H,
                reply_markup=_chip_keyboard(AUDIENCES, s["audience"], "aud"),
            )
        elif action == "extras":
            await query.edit_message_text(
                "✨ <b>Extra Instructions</b> — pick any:", parse_mode=H,
                reply_markup=_chip_keyboard(EXTRAS, s["extras"], "ext"),
            )
        elif action == "avoid":
            _set_await(ctx, "avoid")
            current = f"\n\n<i>Current: {e(s['avoid'])}</i>" if s["avoid"] else ""
            await query.edit_message_text(
                f"🚫 <b>Avoid</b> <i>(optional)</i>{current}\n\n"
                f"E.g. <i>jargon, bullet points, filler</i>\n\nType or Skip:",
                parse_mode=H,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⏭️ Skip", callback_data="skip:avoid"),
                    InlineKeyboardButton("🗑️ Clear", callback_data="clear:avoid"),
                ]]),
            )
        return

    # ── Chip toggle (single + multi) ────────────────────────────────────────
    for prefix, (key, options, is_multi, label, cols) in CHIP_META.items():
        if data.startswith(f"{prefix}:"):
            val = data.split(":", 1)[1]
            if is_multi:
                lst: list = s[key]
                if val in lst:
                    lst.remove(val)
                else:
                    lst.append(val)
            else:
                s[key] = None if s[key] == val else val  # toggle off if same
            hint = "pick any" if is_multi else "pick one"
            await query.edit_message_text(
                f"{label} — <i>{hint}:</i>",
                parse_mode=H,
                reply_markup=_chip_keyboard(options, s[key], prefix, cols),
            )
            return

    # ── Skip ────────────────────────────────────────────────────────────────
    if data.startswith("skip:"):
        _set_await(ctx, None)
        await query.edit_message_text(
            _status_text(s), parse_mode=H, reply_markup=_main_menu(s)
        )
        return

    # ── Clear field ─────────────────────────────────────────────────────────
    if data.startswith("clear:"):
        field = data.split(":", 1)[1]
        _set_await(ctx, None)
        if field in s:
            s[field] = [] if isinstance(s[field], list) else (None if s[field] is None or not isinstance(s[field], str) else "")
        await query.edit_message_text(
            f"🗑️ <i>{e(field.replace('_', ' ').capitalize())} cleared.</i>\n\n" + _status_text(s),
            parse_mode=H,
            reply_markup=_main_menu(s),
        )
        return

    # ── Noop (info buttons) ─────────────────────────────────────────────────
    if data == "noop":
        return


# ─── Text message handler ─────────────────────────────────────────────────────

async def recv_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    s = _state(ctx)
    field = _get_await(ctx)
    text = update.message.text.strip()

    if field in ("task", "role", "context", "avoid"):
        s[field] = text
        _set_await(ctx, None)
        # After task is set, offer to generate if mode also set
        extra = ""
        if field == "task" and s.get("mode"):
            extra = "\n\n<i>Ready to generate! Tap ✨ Generate Prompt below.</i>"
        await update.message.reply_text(
            _status_text(s) + extra,
            parse_mode=H,
            reply_markup=_main_menu(s),
        )
        return

    # Not waiting for anything — show status with helpful hint
    await update.message.reply_text(
        _status_text(s),
        parse_mode=H,
        reply_markup=_main_menu(s),
    )


# ─── Global error handler ─────────────────────────────────────────────────────

async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Fix #4: Catch all exceptions, log them, notify user."""
    log.error("Exception while handling update:", exc_info=ctx.error)
    tb = "".join(traceback.format_exception(None, ctx.error, ctx.error.__traceback__))
    log.error(tb)

    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "⚠️ <b>Something went wrong.</b>\n\n"
            "<i>Please try again or use /start to reset.</i>",
            parse_mode=H,
        )


# ─── Bot commands for autocomplete ────────────────────────────────────────────

BOT_COMMANDS = [
    BotCommand("start",    "Start fresh — choose mode"),
    BotCommand("new",      "New prompt"),
    BotCommand("status",   "See current settings"),
    BotCommand("generate", "Generate prompt now"),
    BotCommand("cancel",   "Cancel current input"),
    BotCommand("help",     "Show help"),
]


# ─── App setup ────────────────────────────────────────────────────────────────

def main() -> None:
    # Fix #8: Persistence — state survives restarts and redeploys
    PERSISTENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    persistence = PicklePersistence(filepath=str(PERSISTENCE_FILE))

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .persistence(persistence)
        .build()
    )

    # Fix #9: Register commands for Telegram autocomplete
    async def post_init(application: Application) -> None:
        await application.bot.set_my_commands(BOT_COMMANDS)
        log.info("Bot commands registered.")

    app.post_init = post_init

    # Fix #4: Global error handler
    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("new",      cmd_new))
    app.add_handler(CommandHandler("status",   cmd_status))
    app.add_handler(CommandHandler("generate", cmd_generate))
    app.add_handler(CommandHandler("cancel",   cmd_cancel))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, recv_text))

    log.info("Prompt Engine Bot v2 starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
