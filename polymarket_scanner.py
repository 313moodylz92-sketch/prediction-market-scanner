import requests
import pandas as pd
import json
import re
from datetime import datetime, timezone

# ── CONFIG ────────────────────────────────────────────────────────────────────
GAMMA_API       = "https://gamma-api.polymarket.com"
MIN_LIQUIDITY   = 1000      # minimum USDC liquidity to consider
MIN_VOLUME_24H  = 500       # minimum 24h volume
MAX_DAYS_LEFT   = 30        # ignore markets resolving too far out
MIN_DAYS_LEFT   = 1         # ignore markets resolving today (too late)
FETCH_LIMIT     = 500       # markets to pull from API

# Categories to show (all others hidden). Set to None to show everything.
SHOW_CATEGORIES = ["Commodities", "World Events", "Crypto", "Politics"]
# SHOW_CATEGORIES = None  # uncomment to see all including sports

# ── DANGER FILTER ─────────────────────────────────────────────────────────────
# Flags resolution traps before you bet. The dragon is knowing which good trades are traps.

DANGER_PATTERNS = [
    (3, r"resolve 50.?50",           "auto 50-50 on no-result"),
    (3, r"if (the match|game|event) (is |are )?(canceled|cancelled|postponed|not (completed|played))", "cancellation = 50-50 trap"),
    (3, r"delayed beyond \d+ days",  "delay = 50-50 trap"),
    (2, r"at (the )?discretion",     "subjective resolution"),
    (2, r"(may|might) resolve",      "uncertain resolution logic"),
    (2, r"unless .{0,60} (in which|otherwise)", "conditional clause trap"),
    (2, r"(oracle|chainlink|uma bond)", "oracle-dependent — can lag or fail"),
    (2, r"provided that",            "conditional wording"),
    (1, r"(approximately|roughly|substantially)", "vague threshold"),
    (1, r"(official|final) (statistics|results|data)", "needs official confirmation"),
    (1, r"(political|government|congress|senate|house|executive)", "political — resolution can be disputed"),
]

SAFE_PATTERNS = [
    r"will (the )?(price of )?(\w+ )?be (above|below|over|under|greater|less) \$?[\d,]+",
    r"will .+ win (the )?[\w\s]+ (on|by) \d{4}-\d{2}-\d{2}",
    r"will .+ (beat|defeat) .+ (on|by) \d{4}-\d{2}-\d{2}",
]

# ── CATEGORY ──────────────────────────────────────────────────────────────────
CATEGORY_PATTERNS = {
    "Commodities": [
        r"\b(gold|silver|xau|xag|crude|wti|brent|oil|copper|wheat|corn|natural gas|natgas|palladium|platinum|commodity|commodities)\b",
    ],
    "Crypto": [
        r"\b(bitcoin|btc|ethereum|eth|solana|sol|dogecoin|doge|xrp|ripple|cardano|ada|bnb|binance|crypto|altcoin|defi|nft|stablecoin|usdc|usdt|tether|coinbase|memecoin)\b",
    ],
    "World Events": [
        r"\b(war|conflict|ceasefire|invasion|treaty|sanctions|nuclear|nato|un |united nations|fed |federal reserve|interest rate|gdp|inflation|cpi|recession|tariff|trade deal|g7|g20|imf|world bank|opec|climate|earthquake|hurricane|summit|election(?! (winner|score))|vote|referendum|president(?!ial cup)|prime minister|chancellor|parliament|congress(?! (score|win))|senate(?! (race score))|eu |european union|china|russia|ukraine|israel|iran|taiwan|north korea|south korea|middle east|nato)\b",
    ],
    "Politics": [
        r"\b(trump|biden|harris|desantis|republican|democrat|gop|white house|inauguration|impeach|cabinet|supreme court|poll(?!e)|approval rating|midterm|ballot|senate race|house race|electoral)\b",
    ],
    "Sports": [
        r"\b(nba|nfl|mlb|nhl|mls|soccer|football|basketball|baseball|hockey|tennis|ufc|mma|boxing|golf|formula 1|f1|cricket|rugby|olympics|championship|league|tournament|playoff|super bowl|world cup|match|game|vs\.? |beat |defeat |score|winner|mvp|draft pick|transfer)\b",
    ],
}

CATEGORY_ORDER = ["Commodities", "World Events", "Politics", "Crypto", "Sports", "Other"]

def categorize_market(m):
    text = (m.get("question", "") + " " + m.get("description", "")).lower()
    for category, patterns in CATEGORY_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text):
                return category
    return "Other"


# ── DANGER FILTER ─────────────────────────────────────────────────────────────
def danger_score(m):
    text = (m.get("question", "") + " " + m.get("description", "")).lower()

    total_danger = 0
    flags = []

    for weight, pattern, label in DANGER_PATTERNS:
        if re.search(pattern, text):
            total_danger += weight
            flags.append(label)

    # Safe pattern bonus — reduces danger
    for pattern in SAFE_PATTERNS:
        if re.search(pattern, text):
            total_danger = max(0, total_danger - 1)

    if total_danger == 0:
        return "🟢 SAFE", flags
    elif total_danger <= 2:
        return "🟡 CAUTION", flags
    else:
        return "🔴 DANGER", flags


# ── SCORING ───────────────────────────────────────────────────────────────────
def score_market(m):
    liquidity  = float(m.get("liquidityNum", 0) or 0)
    volume_24h = float(m.get("volume24hr", 0) or 0)
    end_date   = m.get("endDateIso", "")

    # Parse YES price
    try:
        prices    = json.loads(m.get("outcomePrices", "[0.5,0.5]"))
        yes_price = float(prices[0])
    except Exception:
        yes_price = 0.5

    # Days to resolution
    try:
        end   = datetime.fromisoformat(end_date)
        today = datetime.now(timezone.utc).date()
        if not end.tzinfo:
            end = end.replace(tzinfo=timezone.utc)
        days_left = (end.date() - today).days
    except Exception:
        days_left = -1

    if days_left < MIN_DAYS_LEFT or days_left > MAX_DAYS_LEFT:
        return None, days_left, yes_price

    # Liquidity score (0-100) — log scale so $100k isn't 100x better than $10k
    liq_score = min(liquidity / MIN_LIQUIDITY / 10 * 100, 100)

    # Volume score (0-100)
    vol_score = min(volume_24h / MIN_VOLUME_24H / 5 * 100, 100)

    # Timing score — sweet spot 3-14 days
    if 3 <= days_left <= 14:
        time_score = 90
    elif days_left <= 3:
        time_score = 50
    else:
        time_score = max(0, 90 - (days_left - 14) * 3)

    # Uncertainty score — prices near 50% = max uncertainty = most research edge
    # Prices near 0% or 100% = market has already decided
    uncertainty = 100 - abs(yes_price - 0.5) * 200

    score = (
        liq_score   * 0.35 +
        vol_score   * 0.25 +
        time_score  * 0.20 +
        uncertainty * 0.20
    )

    return round(score, 1), days_left, yes_price


# ── TIER ──────────────────────────────────────────────────────────────────────
def get_tier(score, yes_price, liquidity):
    if score >= 70 and liquidity >= 10000:
        return "🟢 PRIME"
    if score >= 45 and liquidity >= 2000:
        return "🟡 WATCH"
    return "🔴 SKIP"


# ── FETCH ─────────────────────────────────────────────────────────────────────
def fetch_markets():
    all_markets = []
    offset = 0
    while len(all_markets) < FETCH_LIMIT:
        try:
            resp = requests.get(f"{GAMMA_API}/markets", params={
                "limit":      100,
                "offset":     offset,
                "active":     "true",
                "closed":     "false",
                "order":      "volume",
                "ascending":  "false",
            }, timeout=15)
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            all_markets.extend(batch)
            offset += len(batch)
            if len(batch) < 100:
                break
        except Exception as e:
            print(f"  ⚠️  API error: {e}")
            break
    return all_markets


# ── MAIN ──────────────────────────────────────────────────────────────────────
def run_scanner():
    print("\n" + "═" * 72)
    print("  🎯  POLYMARKET INTELLIGENCE SCANNER")
    print(f"  Filters: Liquidity ≥${MIN_LIQUIDITY:,} | 24h Vol ≥${MIN_VOLUME_24H:,} | "
          f"{MIN_DAYS_LEFT}–{MAX_DAYS_LEFT} days to resolve")
    print("═" * 72)

    print("  Fetching active markets from Polymarket...")
    raw = fetch_markets()
    print(f"  Pulled {len(raw)} markets — scoring now...\n")

    results = []
    skipped_restricted = 0

    for m in raw:
        # Skip markets not accepting orders
        if not m.get("acceptingOrders", True):
            continue
        if float(m.get("liquidityNum", 0) or 0) < MIN_LIQUIDITY:
            continue
        if float(m.get("volume24hr", 0) or 0) < MIN_VOLUME_24H:
            continue

        score, days_left, yes_price = score_market(m)
        if score is None:
            continue

        tier = get_tier(score, yes_price, float(m.get("liquidityNum", 0) or 0))
        if tier == "🔴 SKIP":
            continue

        danger, flags = danger_score(m)
        category = categorize_market(m)

        if SHOW_CATEGORIES and category not in SHOW_CATEGORIES:
            continue

        results.append({
            "Tier":          tier,
            "Category":      category,
            "Score":         score,
            "Danger":        danger,
            "Flags":         " | ".join(flags) if flags else "—",
            "Question":      m.get("question", "")[:90],
            "YES %":         f"{yes_price:.1%}",
            "NO %":          f"{1 - yes_price:.1%}",
            "Liquidity ($)": round(float(m.get("liquidityNum", 0) or 0)),
            "Vol 24h ($)":   round(float(m.get("volume24hr", 0) or 0)),
            "Days Left":     days_left,
            "URL":           f"https://polymarket.com/event/{m.get('slug', '')}",
        })

    if not results:
        print("  ⚠️  No qualifying markets found.")
        print(f"  ({skipped_restricted} markets skipped — US restricted)")
        return

    df = pd.DataFrame(results).sort_values(["Category", "Score"], ascending=[True, False])

    # Sort categories in preferred order
    cat_order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    df["_cat_order"] = df["Category"].map(lambda c: cat_order.get(c, 99))
    df = df.sort_values(["_cat_order", "Score"], ascending=[True, False]).drop(columns=["_cat_order"])

    prime = df[df["Tier"] == "🟢 PRIME"]
    watch = df[df["Tier"] == "🟡 WATCH"]

    cats_found = [c for c in CATEGORY_ORDER if c in df["Category"].values]
    cat_summary = "  |  ".join(
        f"{c}: {len(df[df['Category'] == c])}" for c in cats_found
    )
    print(f"  ✅  {len(df)} qualifying markets | {len(prime)} PRIME | {len(watch)} WATCH")
    print(f"  Categories: {cat_summary}\n")

    active_categories = [c for c in CATEGORY_ORDER if c in df["Category"].values]

    for tier_name, tier_df in [("🟢 PRIME — High confidence opportunities", prime),
                                ("🟡 WATCH — Worth monitoring", watch)]:
        if tier_df.empty:
            continue
        print(f"\n  {'═' * 68}")
        print(f"  {tier_name} ({len(tier_df)} markets)")

        for cat in active_categories:
            cat_slice = tier_df[tier_df["Category"] == cat].head(5)
            if cat_slice.empty:
                continue
            print(f"\n  ── {cat} ──────────────────────────────────────────")
            for _, row in cat_slice.iterrows():
                print(f"\n  {row['Question']}")
                print(f"  YES: {row['YES %']}  |  NO: {row['NO %']}  |  "
                      f"Liq: ${row['Liquidity ($)']:,}  |  "
                      f"Vol: ${row['Vol 24h ($)']:,}  |  "
                      f"Days: {row['Days Left']}  |  Score: {row['Score']}")
                print(f"  Resolution risk: {row['Danger']}  {('⚠️  ' + row['Flags']) if row['Flags'] != '—' else ''}")
                print(f"  🔗 {row['URL']}")
        print()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    output = f"/Users/moet/polymarket-scanner/results_{timestamp}.xlsx"
    df.to_excel(output, index=False)
    print(f"  💾  Saved to: {output}")
    print("═" * 72 + "\n")


if __name__ == "__main__":
    run_scanner()
