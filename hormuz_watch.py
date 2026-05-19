import urllib.request
import json
import re
import requests
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
from dotenv import load_dotenv
import os

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
EIA_API_KEY        = os.getenv("EIA_API_KEY")

KALSHI_TICKER = "KXHORMUZNORM-26MAR17-B260615"
ENTRY_COST    = 105.00
ENTRY_PRICE   = 0.1063
CONTRACTS     = 987.7
PAYOUT        = 987.70

# ── HELPERS ───────────────────────────────────────────────────────────────────
def fetch(url, headers=None):
    h = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    if headers:
        h.update(headers)
    req  = urllib.request.Request(url, headers=h)
    resp = urllib.request.urlopen(req, timeout=12)
    return resp.read()

def yahoo_price(ticker):
    url  = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=5d"
    data = json.loads(fetch(url))
    meta = data["chart"]["result"][0]["meta"]
    price = meta.get("regularMarketPrice", 0)
    prev  = meta.get("chartPreviousClose", price)
    chg   = ((price - prev) / prev * 100) if prev else 0
    return price, chg

def kalshi_market(ticker):
    url  = f"https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}"
    data = json.loads(fetch(url))
    m    = data.get("market", {})
    yes_ask  = float(m.get("yes_ask_dollars", 0) or 0)
    yes_bid  = float(m.get("yes_bid_dollars", 0) or 0)
    oi       = float(m.get("open_interest_fp", 0) or 0)
    vol_24h  = float(m.get("volume_24h_fp", 0) or 0)
    spread   = round(yes_ask - yes_bid, 4)
    mid      = round((yes_ask + yes_bid) / 2, 4) if yes_bid > 0 else yes_ask
    return yes_ask, yes_bid, mid, spread, oi, vol_24h

def get_news():
    headlines = []
    sources = [
        ("https://feeds.bbci.co.uk/news/world/rss.xml",       "BBC"),
        ("https://www.aljazeera.com/xml/rss/all.xml",          "Al Jazeera"),
        ("https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "NYT"),
    ]
    keywords = ["iran", "hormuz", "nuclear", "strait", "tanker",
                "sanctions", "ceasefire", "oil", "opec", "houthi"]
    for url, source in sources:
        try:
            data = fetch(url, {"Accept": "application/rss+xml"})
            root = ET.fromstring(data)
            for item in root.findall(".//item"):
                title = item.find("title")
                desc  = item.find("description")
                pub   = item.find("pubDate")
                t = title.text if title is not None else ""
                d = desc.text  if desc  is not None else ""
                combined = (t + " " + d).lower()
                if any(k in combined for k in keywords):
                    headlines.append({
                        "source": source,
                        "title":  t[:100],
                        "date":   pub.text[:22] if pub is not None and pub.text else "",
                    })
        except Exception:
            pass
    return headlines[:5]

def send_telegram_alert(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=8)
    except Exception:
        pass

def get_portwatch_ma():
    # PortWatch doesn't expose a public REST API — scrape their chokepoints page
    try:
        html = fetch("https://portwatch.imf.org/pages/chokepoints").decode("utf-8", errors="ignore")
        # Look for numbers near "Hormuz" in the page data
        idx = html.lower().find("hormuz")
        if idx > 0:
            snippet = html[max(0, idx - 500):idx + 1000]
            numbers = re.findall(r'\b(\d{2,3}(?:\.\d+)?)\b', snippet)
            # Filter to plausible transit call counts (20-120 range)
            plausible = [float(n) for n in numbers if 20 <= float(n) <= 120]
            if plausible:
                return plausible[0], True
    except Exception:
        pass
    return None, False

def get_eia_inventory():
    if not EIA_API_KEY:
        return None, None, None
    try:
        url = (
            f"https://api.eia.gov/v2/petroleum/sum/sndw/data/"
            f"?api_key={EIA_API_KEY}&frequency=weekly&data[0]=value"
            f"&facets[series][]=WCRSTUS1&sort[0][column]=period&sort[0][direction]=desc&length=2"
        )
        data = json.loads(fetch(url))
        rows = data["response"]["data"]
        if len(rows) < 2:
            return None, None, None
        latest   = rows[0]
        previous = rows[1]
        current  = float(latest["value"])
        prev_val = float(previous["value"])
        change   = current - prev_val
        date     = latest["period"]
        return current, change, date
    except Exception:
        return None, None, None

# ── MAIN ──────────────────────────────────────────────────────────────────────
def run():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print("\n" + "═" * 68)
    print(f"  🔭  HORMUZ WATCH  —  {now}")
    print("═" * 68)

    # ── TRADE STATUS ──────────────────────────────────────────────────────
    print("\n  📊  TRADE #1 — Hormuz YES  (resolves June 15, 2026)")
    print(f"  Entry: {ENTRY_PRICE:.2%} avg  |  Contracts: {CONTRACTS}  |  Cost: ${ENTRY_COST:.2f}")
    print(f"  Payout if right: ${PAYOUT:.2f}  (+${PAYOUT - ENTRY_COST:.2f})")

    alerts = []
    score_yes_ask = None
    score_spread  = None
    score_ma      = None
    score_brent   = None
    score_ovx     = None

    try:
        yes_ask, yes_bid, mid, spread, oi, vol_24h = kalshi_market(KALSHI_TICKER)
        score_yes_ask = yes_ask
        score_spread  = spread
        current_val  = mid * CONTRACTS
        unrealized   = current_val - ENTRY_COST
        unrealized_p = (unrealized / ENTRY_COST) * 100
        print(f"\n  Kalshi NOW:  YES ask ${yes_ask:.2f}  |  YES bid ${yes_bid:.2f}  |  Mid ${mid:.2f}")
        print(f"  Spread: ${spread:.3f}  |  OI: ${oi:,.0f}  |  Vol 24h: ${vol_24h:,.0f}")
        print(f"  Position value: ${current_val:.2f}  |  Unrealized: {unrealized:+.2f} ({unrealized_p:+.1f}%)")

        if spread <= 0.03:
            print("  ⚡  SPREAD TIGHT — smart money may be pricing in YES")
            alerts.append(f"⚡ HORMUZ SPREAD TIGHT (${spread:.3f}) — smart money pricing in YES. Mid ${mid:.2f}.")
        elif spread >= 0.08:
            print("  ⚠️  SPREAD WIDE — low conviction, thin market")
            alerts.append(f"⚠️ HORMUZ SPREAD WIDE (${spread:.3f}) — thin market, low conviction. Mid ${mid:.2f}.")

        if yes_ask >= 0.20:
            print(f"  🚨  PRICE UP — YES at ${yes_ask:.2f}. Consider selling to lock gain.")
            alerts.append(f"🚨 HORMUZ YES PRICE UP to ${yes_ask:.2f} — consider selling to lock gain. Position: ${current_val:.2f} ({unrealized_p:+.1f}%).")
        elif yes_ask <= 0.05:
            print(f"  📉  PRICE DOWN — market losing faith in thesis. Reassess.")
            alerts.append(f"📉 HORMUZ YES PRICE DOWN to ${yes_ask:.2f} — market losing faith. Reassess thesis.")
    except Exception as e:
        print(f"  ⚠️  Kalshi fetch error: {e}")

    # ── RESOLUTION METRIC ─────────────────────────────────────────────────
    print("\n  ── Resolution Metric (IMF PortWatch) ──────────────────────────")
    ma, found = get_portwatch_ma()
    if found and ma:
        score_ma = ma
    if found and ma:
        gap     = 60 - ma
        pct_gap = (gap / 60) * 100
        bar_val = int((ma / 60) * 20)
        bar     = "█" * bar_val + "░" * (20 - bar_val)
        print(f"  Hormuz 7-day MA: ~{ma:.0f} transits  (threshold: 60)")
        print(f"  Progress: [{bar}] {ma:.0f}/60  —  {gap:.0f} calls short ({pct_gap:.0f}% below threshold)")
        if ma >= 58:
            print("  🚨  NEAR THRESHOLD — resolution possible soon!")
        elif ma >= 45:
            print("  📈  IMPROVING — moving toward threshold")
        else:
            print("  📉  FAR FROM THRESHOLD — needs significant recovery")
    else:
        print("  ⚠️  Auto-fetch unavailable — check manually:")
        print("  🔗  https://portwatch.imf.org/pages/chokepoints")
        print("      Look for 'Strait of Hormuz' → 7-day moving average → needs to be > 60")

    # ── OIL + VOLATILITY ──────────────────────────────────────────────────
    print("\n  ── Oil & Volatility ─────────────────────────────────────────────")
    try:
        brent, brent_chg = yahoo_price("BZ=F")
        wti,   wti_chg   = yahoo_price("CL=F")
        ovx,   ovx_chg   = yahoo_price("^OVX")
        score_brent = brent_chg
        score_ovx   = ovx
        print(f"  Brent Crude:    ${brent:.2f}  ({brent_chg:+.1f}%)")
        print(f"  WTI Crude:      ${wti:.2f}   ({wti_chg:+.1f}%)")
        print(f"  Oil Volatility: {ovx:.1f}     ({ovx_chg:+.1f}%)")

        if brent_chg >= 3:
            print("  ⚡  BRENT SPIKE — possible supply disruption signal")
            alerts.append(f"⚡ BRENT SPIKE +{brent_chg:.1f}% to ${brent:.2f} — possible supply disruption. Watch Hormuz YES.")
        if ovx >= 50:
            print("  ⚠️  HIGH OIL VOL — market nervous about supply")
            alerts.append(f"⚠️ HIGH OIL VOL (OVX {ovx:.1f}) — market pricing supply risk. Watch Hormuz YES.")
    except Exception as e:
        print(f"  ⚠️  Oil data error: {e}")

    # ── EIA CRUDE INVENTORY ───────────────────────────────────────────────
    print("\n  ── EIA Crude Inventory (weekly) ─────────────────────────────────")
    eia_current, eia_change, eia_date = get_eia_inventory()
    if eia_current is not None:
        direction = "BUILD" if eia_change > 0 else "DRAW"
        arrow     = "📈" if eia_change > 0 else "📉"
        print(f"  {arrow}  US Crude Stocks: {eia_current:,.0f} Mbbl  ({eia_change:+,.0f} Mbbl {direction})  [{eia_date}]")
        if eia_change <= -3000:
            print("  ⚡  LARGE DRAW — supply tightening, bullish for disruption thesis")
            alerts.append(f"⚡ EIA LARGE DRAW {eia_change:+,.0f} Mbbl — supply tightening. Hormuz disruption gaining weight.")
        elif eia_change >= 3000:
            print("  ⚠️  LARGE BUILD — supply ample, bearish for disruption thesis")
    else:
        print("  ⚠️  EIA data unavailable")

    # ── NEWS ──────────────────────────────────────────────────────────────
    print("\n  ── News (Iran / Hormuz / Oil) ───────────────────────────────────")
    headlines = get_news()
    if headlines:
        for h in headlines:
            print(f"  [{h['source']}] {h['title']}")
    else:
        print("  No relevant headlines found.")

    # ── THESIS CHECK ──────────────────────────────────────────────────────
    print("\n  ── Thesis: US-Iran slight easing → Hormuz normalizes > 60 MA ───")
    print("  Watch for: nuclear deal framework, ceasefire signal, naval de-escalation")
    print("  Exit trigger: YES price hits $0.20+ → sell early, lock gain")
    print("  Stop signal: YES price drops to $0.04 → reassess thesis")

    # ── RISK SCORE ────────────────────────────────────────────────────────
    score = 50
    reasons = []

    # Kalshi price — market structure signal
    if score_yes_ask is not None:
        if score_yes_ask >= 0.20:   score += 20; reasons.append("Price up +20")
        elif score_yes_ask >= 0.15: score += 12; reasons.append("Price rising +12")
        elif score_yes_ask >= 0.10: score += 5;  reasons.append("Price flat +5")
        elif score_yes_ask <= 0.05: score -= 25; reasons.append("Price collapse -25")
        elif score_yes_ask <= 0.07: score -= 12; reasons.append("Price weak -12")

    # Spread — tradability signal only
    if score_spread is not None:
        if score_spread <= 0.03:   score += 5;  reasons.append("Spread tight +5")
        elif score_spread >= 0.08: score -= 5;  reasons.append("Spread wide -5")

    # PortWatch MA — biggest thesis signal
    if score_ma is not None:
        if score_ma >= 58:   score += 25; reasons.append("MA near threshold +25")
        elif score_ma >= 45: score += 15; reasons.append("MA improving +15")
        elif score_ma >= 30: score -= 5;  reasons.append("MA lagging -5")
        elif score_ma >= 20: score -= 15; reasons.append("MA far off -15")
        else:                score -= 20; reasons.append("MA critical -20")
    else:
        score -= 5; reasons.append("MA unknown -5")

    # Oil/volatility — context signals, not decisive
    if score_brent is not None:
        if score_brent >= 5:   score -= 8; reasons.append("Brent spike -8")
        elif score_brent >= 3: score -= 5; reasons.append("Brent elevated -5")

    if score_ovx is not None:
        if score_ovx >= 70:   score -= 8; reasons.append("OVX extreme -8")
        elif score_ovx >= 50: score -= 5; reasons.append("OVX high -5")
        elif score_ovx < 30:  score += 3; reasons.append("OVX calm +3")

    score = max(0, min(100, score))

    if score >= 80:   status_icon, status_label = "🟢", "STRONG YES CONDITIONS"
    elif score >= 60: status_icon, status_label = "🟢", "FAVORABLE"
    elif score >= 40: status_icon, status_label = "🟡", "WATCH"
    elif score >= 25: status_icon, status_label = "🟠", "HIGH RISK"
    else:             status_icon, status_label = "🔴", "THESIS DAMAGE"

    print(f"\n  ── War Room Risk Score ──────────────────────────────────────────")
    print(f"  {status_icon}  SCORE: {score}/100  —  {status_label}")
    print(f"  Factors: {', '.join(reasons) if reasons else 'Baseline only'}")
    print(f"  Score is a monitoring signal, not a trade instruction.")

    # ── TELEGRAM ──────────────────────────────────────────────────────────
    score_line = f"{status_icon} WAR ROOM SCORE: {score}/100 — {status_label}"
    if alerts:
        full_msg = f"🔭 HORMUZ WATCH — {now}\n\n" + "\n\n".join(alerts) + f"\n\n{score_line}"
        send_telegram_alert(full_msg)
        print(f"\n  📲  Telegram alert sent ({len(alerts)} signal(s))")
    else:
        send_telegram_alert(f"🔭 HORMUZ WATCH — {now}\n\n{score_line}\nNo triggers fired.")
        print(f"\n  📲  Telegram update sent — no triggers, score only.")

    print("\n" + "═" * 68 + "\n")

if __name__ == "__main__":
    run()
