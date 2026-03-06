# 🚗 Copart Monitor

Automatically monitors Copart US for new car listings matching your criteria and sends Telegram notifications. Runs every 2 hours via GitHub Actions.

## How It Works

```
GitHub Actions (every 2h)
        │
        ▼
Try Copart API  ──── success? ──▶  Parse lots
        │ fail/empty                    │
        ▼                               │
Playwright fallback ────────────────────┘
        │
        ▼
Compare against state.json (seen lots)
        │
        ▼
New lots? ──── yes ──▶ Send Telegram notification(s)
        │                       │
        ▼                       ▼
Update & commit state.json to repo
```

## Setup

### 1. Create your GitHub repository

```bash
git init copart-monitor
cd copart-monitor
# copy all these files in
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/copart-monitor.git
git push -u origin main
```

### 2. Create a Telegram Bot

1. Open Telegram and message **@BotFather**
2. Send `/newbot` and follow prompts
3. Copy the **Bot Token** (looks like `123456789:ABCdef...`)
4. Start a chat with your new bot (send it any message)
5. Get your **Chat ID**:
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
   Look for `"chat":{"id":XXXXXXXXX}` in the response

### 3. Add GitHub Secrets

Go to your repo → **Settings** → **Secrets and variables** → **Actions**

Add these **Secrets** (sensitive values):

| Secret Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Your chat ID number |

### 4. Add GitHub Variables

Add these **Variables** (non-sensitive config):

| Variable Name | Example Value | Description |
|---|---|---|
| `COPART_MAKES` | `BMW,Toyota,Honda` | Comma-separated makes to monitor |
| `COPART_DAMAGE_TYPES` | `FRONT END,REAR END,HAIL` | Comma-separated damage types |
| `COPART_MAX_PAGES` | `3` | How many result pages to fetch (default: 3) |

**Common Copart damage types:**
- `FRONT END`
- `REAR END`
- `ALL OVER`
- `HAIL`
- `MECHANICAL`
- `VANDALISM`
- `FLOOD`
- `FIRE`
- `ROLLOVER`
- `UNDERCARRIAGE`
- `MINOR DENTS/SCRATCHES`

### 5. Test the Setup

1. Go to **Actions** tab in your GitHub repo
2. Click **Copart Monitor** workflow
3. Click **Run workflow** → enable **"Send a test Telegram message only"** → **Run**
4. You should receive a test message in Telegram ✅

### 6. Run Your First Monitor Check

1. In **Actions** → **Run workflow** → **Run** (default settings)
2. Check the logs to see how many lots were found
3. New listings will be sent to Telegram

The workflow will then run automatically every 2 hours.

## Project Structure

```
copart-monitor/
├── .github/
│   └── workflows/
│       └── monitor.yml      # GitHub Actions — runs every 2h
├── src/
│   ├── copart_api.py        # Copart unofficial API client (fast path)
│   ├── copart_playwright.py # Playwright browser fallback (reliable path)
│   ├── notifier.py          # Telegram message sender
│   └── state_manager.py     # Seen-lots tracking logic
├── monitor.py               # Main entry point
├── state.json               # Persisted seen lot numbers (auto-committed)
├── requirements.txt
└── README.md
```

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Set environment variables
export TELEGRAM_BOT_TOKEN="your_token"
export TELEGRAM_CHAT_ID="your_chat_id"
export COPART_MAKES="BMW,Toyota"
export COPART_DAMAGE_TYPES="FRONT END,HAIL"

# Test Telegram
python monitor.py --test-telegram

# Dry run (no notifications, no state changes)
python monitor.py --dry-run

# Normal run
python monitor.py
```

## Adjusting the Schedule

Edit `.github/workflows/monitor.yml` and change the cron expression:

```yaml
- cron: "0 */2 * * *"   # Every 2 hours (default)
- cron: "0 */1 * * *"   # Every hour
- cron: "0 9,17 * * *"  # Twice daily at 9am and 5pm UTC
- cron: "0 9 * * 1-5"   # Weekdays at 9am UTC
```

> ⚠️ GitHub Actions free tier has a limit of ~2000 minutes/month. Running every 2 hours uses ~360 min/month — well within limits.

## Troubleshooting

| Problem | Solution |
|---|---|
| No lots found | Check `COPART_MAKES` / `COPART_DAMAGE_TYPES` spelling (case-insensitive) |
| Telegram not sending | Re-check token and chat_id; use **Run workflow → test_telegram=true** |
| API always failing | Copart may have changed their API — Playwright fallback will handle it |
| state.json not committing | Ensure the workflow has `permissions: contents: write` (already set) |
| Too many notifications | Reduce `COPART_MAX_PAGES` or add more specific filters |
