import requests
import subprocess
import os
import time
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID"))
SCRIPT  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hormuz_watch.py")

BASE = f"https://api.telegram.org/bot{TOKEN}"

HELP_TEXT = (
    "🔭 HORMUZ WAR ROOM — Commands\n\n"
    "/report — full signal dashboard\n"
    "/score  — War Room score only\n"
    "/help   — this message"
)

def send(text):
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        try:
            requests.post(f"{BASE}/sendMessage", json={"chat_id": CHAT_ID, "text": chunk}, timeout=10)
        except Exception:
            pass

def get_updates(offset):
    try:
        r = requests.get(f"{BASE}/getUpdates", params={"offset": offset, "timeout": 30}, timeout=35)
        return r.json().get("result", [])
    except Exception:
        return []

def run_report():
    send("⏳ Running report...")
    try:
        result = subprocess.run(
            ["python3", SCRIPT],
            capture_output=True, text=True, timeout=60
        )
        output = result.stdout.strip()
        if not output:
            output = result.stderr.strip() or "No output."
        send(output)
    except subprocess.TimeoutExpired:
        send("⚠️ Report timed out.")
    except Exception as e:
        send(f"⚠️ Error: {e}")

def get_score_line():
    try:
        result = subprocess.run(
            ["python3", SCRIPT],
            capture_output=True, text=True, timeout=60
        )
        for line in result.stdout.splitlines():
            if "SCORE" in line or "THESIS" in line or "FAVORABLE" in line or "WATCH" in line or "STRONG" in line or "HIGH RISK" in line:
                send(line.strip())
                return
        send("Score not found — run /report for full output.")
    except Exception as e:
        send(f"⚠️ Error: {e}")

def handle(text):
    t = text.strip().lower()
    if t in ("/report", "report"):
        run_report()
    elif t in ("/score", "score"):
        get_score_line()
    elif t in ("/help", "help", "hi", "hello"):
        send(HELP_TEXT)
    else:
        send("Unknown command. Send /help to see what I can do.")

def main():
    print("Bot listener running — waiting for messages...")
    send("🟢 Hormuz War Room online. Send /help for commands.")
    offset = 0
    while True:
        updates = get_updates(offset)
        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text = msg.get("text", "")
            if chat_id == CHAT_ID and text:
                handle(text)
        time.sleep(1)

if __name__ == "__main__":
    main()
