# Prediction Market Scanner Template

Find edges in prediction markets before the crowd does. Track your open trades with a live signal dashboard. Get alerts on your phone automatically.

Built by [313SESSIONS](https://313sessions.vercel.app) — the marketplace for AI tools.

---

## What's included

| File | What it does |
|------|-------------|
| `kalshi_scanner.py` | Scans US Kalshi markets, scores by opportunity, flags traps |
| `polymarket_scanner.py` | Scans international Polymarket markets |
| `hormuz_watch.py` | Live signal dashboard for an open trade — Kalshi price, oil data, news, War Room score |
| `bot_listener.py` | Two-way Telegram bot — text `/report` anytime, get live signals back |

---

## Setup

```bash
pip install requests pandas openpyxl python-dotenv
```

Create a `.env` file in the same folder:

```
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
EIA_API_KEY=your_eia_key
```

**Get your keys:**
- Telegram bot token + chat ID: message `@BotFather` on Telegram → `/newbot`
- EIA API key (free): eia.gov/opendata → Register

---

## Run the scanners

```bash
python3 kalshi_scanner.py       # US markets
python3 polymarket_scanner.py   # International markets
```

Exports results to Excel. Scores 0–100, tiers PRIME/WATCH, flags resolution traps.

---

## Run the signal dashboard

```bash
python3 hormuz_watch.py
```

Shows:
- Kalshi YES/NO price, spread, OI, unrealized P&L
- IMF PortWatch Hormuz transit MA vs threshold
- Brent crude, WTI, OVX oil volatility
- EIA weekly crude inventory (draw/build signal)
- Iran/Hormuz RSS news headlines
- War Room Risk Score (0–100) with status label

Adapt `hormuz_watch.py` for any trade — swap the ticker, resolution metric, and news keywords.

---

## Run the Telegram bot (two-way)

```bash
python3 bot_listener.py
```

Text your bot from your phone:
- `/report` — full signal dashboard delivered to Telegram
- `/score` — War Room score only
- `/help` — command list

To auto-start on Mac login, add a launchd plist pointing to `bot_listener.py`.

---

## Auto-run daily (Mac)

```bash
crontab -e
```

Add:
```
3 4 * * * /usr/bin/python3 /path/to/hormuz_watch.py >> /path/to/hormuz_watch.log 2>&1
```

Fires every morning at 4:03 AM. Telegram alerts hit your phone automatically.

---

## Customize the scanners

Edit the config block at the top of either scanner:

```python
MIN_OPEN_INT    = 5000   # minimum liquidity
MIN_VOLUME_24H  = 500    # minimum 24h volume
MAX_DAYS_LEFT   = 30     # max days to resolution
SHOW_CATEGORIES = ["Economy", "Commodities", "World Events", "Politics", "Crypto"]
```

---

## License

Personal use only. Do not resell. See LICENSE for full terms.
