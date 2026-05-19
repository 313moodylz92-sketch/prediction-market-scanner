import requests
import pandas as pd
import re
from datetime import datetime, timezone, timedelta

# ── CONFIG ─────────────────────────────────────────────────────────────────────
KALSHI_API      = "https://api.elections.kalshi.com/trade-api/v2"
MIN_OPEN_INT    = 5000      # min open interest ($ locked in market) — proxy for liquidity
MIN_VOLUME_24H  = 500       # min 24h volume in $
MAX_DAYS_LEFT   = 30
MIN_DAYS_LEFT   = 1
FETCH_LIMIT     = 500

# Categories to show. Set to None to see everything.
SHOW_CATEGORIES = ["Economy", "Commodities", "World Events", "Politics", "Crypto"]

# ── CATEGORY ───────────────────────────────────────────────────────────────────
CATEGORY_PATTERNS = {
    "Commodities": [
        r"\b(gold|silver|xau|xag|crude|wti|brent|oil|copper|wheat|corn|natural gas|natgas|palladium|platinum|commodity|commodities)\b",
    ],
    "Crypto": [
        r"\b(bitcoin|btc|ethereum|eth|solana|sol|dogecoin|doge|xrp|ripple|cardano|ada|bnb|binance|crypto|altcoin|defi|nft|stablecoin|usdc|usdt|tether|coinbase|memecoin)\b",
    ],
    "Economy": [
        r"\b(fed |federal reserve|interest rate|gdp|inflation|cpi|pce|recession|tariff|trade (deal|war|deficit)|g7|g20|imf|world bank|opec|jobs report|unemployment|nonfarm|payroll|treasury|yield|debt ceiling|budget|deficit|rate (hike|cut|hold)|basis points|bps)\b",
    ],
    "World Events": [
        r"\b(war|conflict|ceasefire|invasion|treaty|sanctions|nuclear|nato|un |united nations|climate|earthquake|hurricane|summit|referendum|prime minister|chancellor|parliament|eu |european union|china|russia|ukraine|israel|iran|taiwan|north korea|south korea|middle east)\b",
    ],
    "Politics": [
        r"\b(trump|biden|harris|desantis|republican|democrat|gop|white house|inauguration|impeach|cabinet|supreme court|approval rating|midterm|ballot|senate|house|electoral|congress|executive order|veto|legislation|bill passed|reconciliation)\b",
    ],
    "Sports": [
        r"\b(nba|nfl|mlb|nhl|mls|soccer|football|basketball|baseball|hockey|tennis|ufc|mma|boxing|golf|formula 1|f1|cricket|rugby|olympics|championship|league|tournament|playoff|super bowl|world cup)\b",
    ],
}

CATEGORY_ORDER = ["Economy", "Commodities", "World Events", "Politics", "Crypto", "Sports", "Other"]

def categorize_market(title):
    text = title.lower()
    for category, patterns in CATEGORY_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text):
                return category
    return "Other"


# ── DANGER FILTER ──────────────────────────────────────────────────────────────
DANGER_PATTERNS = [
    (3, r"resolve (50.?50|at 50)",              "auto 50-50 on no-result"),
    (3, r"(canceled|cancelled|postponed|not (completed|played|held))", "cancellation = 50-50 trap"),
    (2, r"at (the )?discretion",                "subjective resolution"),
    (2, r"(may|might) resolve",                 "uncertain resolution logic"),
    (2, r"unless .{0,60} (in which|otherwise)", "conditional clause trap"),
    (2, r"provided that",                       "conditional wording"),
    (1, r"(approximately|roughly|substantially)", "vague threshold"),
]

SAFE_PATTERNS = [
    r"(above|below|over|under|higher|lower) than \$?[\d,]+",
    r"(hike|cut|hold) (by |rates by )?\d+ ?(bps|basis)",
    r"(rate|rates) (above|below|at|between) [\d.]+",
    r"reach \$?[\d,]+ (by|before|on)",
]

def danger_score(title):
    text = title.lower()
    total = 0
    flags = []
    for weight, pattern, label in DANGER_PATTERNS:
        if re.search(pattern, text):
            total += weight
            flags.append(label)
    for pattern in SAFE_PATTERNS:
        if re.search(pattern, text):
            total = max(0, total - 1)
    if total == 0:
        return "🟢 SAFE", flags
    elif total <= 2:
        return "🟡 CAUTION", flags
    else:
        return "🔴 DANGER", flags


# ── SCORING ────────────────────────────────────────────────────────────────────
def score_market(m):
    open_int   = float(m.get("open_interest_fp", 0) or 0)
    volume_24h = float(m.get("volume_24h_fp", 0) or 0)
    close_time = m.get("close_time", "")

    # YES mid-price
    try:
        yes_ask = float(m.get("yes_ask_dollars", 0) or 0)
        yes_bid = float(m.get("yes_bid_dollars", 0) or 0)
        if yes_bid > 0 and yes_ask > 0:
            yes_price = (yes_bid + yes_ask) / 2
        elif yes_ask > 0:
            yes_price = yes_ask
        else:
            yes_price = float(m.get("last_price_dollars", 0.5) or 0.5)
    except Exception:
        yes_price = 0.5

    # Days to close
    try:
        end       = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
        today     = datetime.now(timezone.utc).date()
        days_left = (end.date() - today).days
    except Exception:
        days_left = -1

    if days_left < MIN_DAYS_LEFT or days_left > MAX_DAYS_LEFT:
        return None, days_left, yes_price

    liq_score  = min(open_int / MIN_OPEN_INT / 10 * 100, 100)
    vol_score  = min(volume_24h / MIN_VOLUME_24H / 5 * 100, 100)

    if 3 <= days_left <= 14:
        time_score = 90
    elif days_left <= 3:
        time_score = 50
    else:
        time_score = max(0, 90 - (days_left - 14) * 3)

    uncertainty = 100 - abs(yes_price - 0.5) * 200

    score = (
        liq_score   * 0.35 +
        vol_score   * 0.25 +
        time_score  * 0.20 +
        uncertainty * 0.20
    )

    return round(score, 1), days_left, yes_price


# ── TIER ───────────────────────────────────────────────────────────────────────
def get_tier(score, open_int):
    if score >= 70 and open_int >= 50000:
        return "🟢 PRIME"
    if score >= 40 and open_int >= 5000:
        return "🟡 WATCH"
    return "🔴 SKIP"


# ── FETCH ──────────────────────────────────────────────────────────────────────
def fetch_markets():
    all_markets = []
    cursor      = None
    today       = datetime.now(timezone.utc)
    min_ts      = int((today + timedelta(days=MIN_DAYS_LEFT)).timestamp())
    max_ts      = int((today + timedelta(days=MAX_DAYS_LEFT)).timestamp())

    while len(all_markets) < FETCH_LIMIT:
        params = {
            "status":        "open",
            "limit":         200,
            "min_close_ts":  min_ts,
            "max_close_ts":  max_ts,
        }
        if cursor:
            params["cursor"] = cursor
        try:
            resp = requests.get(f"{KALSHI_API}/markets", params=params, timeout=15)
            resp.raise_for_status()
            data   = resp.json()
            batch  = data.get("markets", [])
            if not batch:
                break
            all_markets.extend(batch)
            cursor = data.get("cursor")
            if not cursor or len(batch) < 200:
                break
        except Exception as e:
            print(f"  ⚠️  API error: {e}")
            break
    return all_markets


# ── MAIN ───────────────────────────────────────────────────────────────────────
def run_scanner():
    print("\n" + "═" * 72)
    print("  🎯  KALSHI INTELLIGENCE SCANNER  (US Legal — CFTC Regulated)")
    print(f"  Filters: Open Interest ≥${MIN_OPEN_INT:,} | 24h Vol ≥${MIN_VOLUME_24H:,} | "
          f"{MIN_DAYS_LEFT}–{MAX_DAYS_LEFT} days to resolve")
    print("═" * 72)

    print("  Fetching active markets from Kalshi...")
    raw = fetch_markets()
    print(f"  Pulled {len(raw)} markets — scoring now...\n")

    results = []

    for m in raw:
        if m.get("status") != "active":
            continue

        open_int   = float(m.get("open_interest_fp", 0) or 0)
        volume_24h = float(m.get("volume_24h_fp", 0) or 0)

        if open_int < MIN_OPEN_INT:
            continue
        if volume_24h < MIN_VOLUME_24H:
            continue

        score, days_left, yes_price = score_market(m)
        if score is None:
            continue

        tier = get_tier(score, open_int)
        if tier == "🔴 SKIP":
            continue

        title    = m.get("title", "")
        danger, flags = danger_score(title)
        category = categorize_market(title)

        if SHOW_CATEGORIES and category not in SHOW_CATEGORIES:
            continue

        yes_ask = float(m.get("yes_ask_dollars", 0) or 0)
        no_ask  = float(m.get("no_ask_dollars", 0) or 0)

        results.append({
            "Tier":            tier,
            "Category":        category,
            "Score":           score,
            "Danger":          danger,
            "Flags":           " | ".join(flags) if flags else "—",
            "Question":        title[:90],
            "YES %":           f"{yes_price:.1%}",
            "YES ask":         f"${yes_ask:.2f}",
            "NO ask":          f"${no_ask:.2f}",
            "Open Int ($)":    round(open_int),
            "Vol 24h ($)":     round(volume_24h),
            "Days Left":       days_left,
            "URL":             f"https://kalshi.com/markets/{m.get('event_ticker', '')}",
        })

    if not results:
        print("  ⚠️  No qualifying markets found.")
        print("  Tip: Lower MIN_OPEN_INT or MIN_VOLUME_24H in config.")
        return

    df = pd.DataFrame(results)
    cat_order      = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    df["_cat_ord"] = df["Category"].map(lambda c: cat_order.get(c, 99))
    df = df.sort_values(["_cat_ord", "Score"], ascending=[True, False]).drop(columns=["_cat_ord"])

    prime = df[df["Tier"] == "🟢 PRIME"]
    watch = df[df["Tier"] == "🟡 WATCH"]

    cats_found  = [c for c in CATEGORY_ORDER if c in df["Category"].values]
    cat_summary = "  |  ".join(f"{c}: {len(df[df['Category'] == c])}" for c in cats_found)

    print(f"  ✅  {len(df)} qualifying markets | {len(prime)} PRIME | {len(watch)} WATCH")
    print(f"  Categories: {cat_summary}\n")

    for tier_name, tier_df in [("🟢 PRIME — High confidence opportunities", prime),
                                ("🟡 WATCH — Worth monitoring", watch)]:
        if tier_df.empty:
            continue
        print(f"\n  {'═' * 68}")
        print(f"  {tier_name} ({len(tier_df)} markets)")

        for cat in cats_found:
            cat_slice = tier_df[tier_df["Category"] == cat].head(5)
            if cat_slice.empty:
                continue
            print(f"\n  ── {cat} ──────────────────────────────────────────")
            for _, row in cat_slice.iterrows():
                print(f"\n  {row['Question']}")
                print(f"  YES: {row['YES %']} (ask {row['YES ask']})  |  NO ask: {row['NO ask']}  |  "
                      f"OI: ${row['Open Int ($)']:,}  |  "
                      f"Vol: ${row['Vol 24h ($)']:,}  |  "
                      f"Days: {row['Days Left']}  |  Score: {row['Score']}")
                print(f"  Resolution risk: {row['Danger']}  {('⚠️  ' + row['Flags']) if row['Flags'] != '—' else ''}")
                print(f"  🔗 {row['URL']}")
        print()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    output    = f"/Users/moet/polymarket-scanner/kalshi_results_{timestamp}.xlsx"
    df.to_excel(output, index=False)
    print(f"  💾  Saved to: {output}")
    print("═" * 72 + "\n")


if __name__ == "__main__":
    run_scanner()
