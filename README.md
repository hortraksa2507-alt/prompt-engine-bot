# Prompt Engine Telegram Bot

A professional Telegram bot for crafting perfect Claude prompts with full inline keyboard UI.

## Setup

```bash
cd prompt-engine-bot
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your tokens
```

## .env

```
TELEGRAM_BOT_TOKEN=    # from @BotFather
ALLOWED_USER_IDS=      # comma-separated Telegram user IDs (leave empty to allow all)
```

## Get your Bot Token

1. Open Telegram → search **@BotFather**
2. Send `/newbot`
3. Choose a name and username
4. Copy the token → paste into `.env`

## Get your User ID

Send any message to **@userinfobot** — it replies with your ID.

## Run

```bash
python bot.py
```

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Start fresh, choose mode |
| `/new` | New prompt |
| `/status` | See current settings |
| `/generate` | Generate prompt now |
| `/help` | Help message |

## Bot Flow

```
/start
  → Choose Mode [Write] [Code] [Analyze] [Debug] [Learn] [Brainstorm]
  → Type task description
  → Set Role (optional)
  → Set Context (optional)
  → Advanced Options:
      → Tone, Format, Length, Audience, Extras, Avoid
  → Generate Prompt ✨
  → Copy and paste into Claude!
```
