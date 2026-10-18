import csv
import time
from collections import Counter
from datetime import datetime, date
from pathlib import Path
from typing import Dict, Optional

from config import settings
from notifications.telegram import get_updates, send_telegram

TRADE_JOURNAL = Path(__file__).resolve().parent / "logs" / "trade_journal.csv"


def parse_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def load_today_trade_summary() -> Dict[str, Optional[float]]:
    today = date.today()
    summary = {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "invested": 0.0,
        "pnl": 0.0,
        "symbols": Counter(),
    }

    if not TRADE_JOURNAL.exists():
        return summary

    with TRADE_JOURNAL.open("r", newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            timestamp = row.get("timestamp") or row.get("datetime") or ""
            try:
                trade_date = datetime.fromisoformat(timestamp).date()
            except ValueError:
                try:
                    trade_date = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").date()
                except ValueError:
                    continue
            if trade_date != today:
                continue

            summary["total_trades"] += 1
            pnl = parse_float(row.get("pnl", "0"))
            amount = parse_float(row.get("amount", "0"))
            summary["pnl"] += pnl
            summary["invested"] += amount

            if pnl > 0:
                summary["wins"] += 1
            elif pnl < 0:
                summary["losses"] += 1
            else:
                summary["breakeven"] += 1

            symbol = row.get("symbol") or row.get("ticker") or "UNKNOWN"
            summary["symbols"][symbol] += 1

    return summary


def build_status_message(summary: Dict[str, Optional[float]]) -> str:
    total = summary["total_trades"]
    wins = summary["wins"]
    losses = summary["losses"]
    breakeven = summary["breakeven"]
    invested = summary["invested"]
    pnl = summary["pnl"]
    win_rate = f"{(wins / total * 100):.1f}%" if total else "0.0%"
    avg_trade = f"{(pnl / total):.2f}" if total else "0.00"
    top_symbols = summary["symbols"].most_common(3)
    top_symbols_text = ", ".join([f"{s}({c})" for s, c in top_symbols]) or "N/A"

    return (
        f"📊 Today's trading status:\n"
        f"Total trades: {total}\n"
        f"Wins: {wins} | Losses: {losses} | Breakeven: {breakeven}\n"
        f"Invested: {invested:.2f}\n"
        f"P/L: {pnl:.2f}\n"
        f"Win rate: {win_rate}\n"
        f"Avg P/L per trade: {avg_trade}\n"
        f"Top symbols: {top_symbols_text}"
    )


def handle_command(chat_id: str, text: str) -> None:
    command = text.strip().lower()
    if command.startswith("/status"):
        summary = load_today_trade_summary()
        message = build_status_message(summary)
        send_telegram(message, chat_id=chat_id)
    elif command.startswith("/start") or command.startswith("/help"):
        send_telegram(
            "Send /status to get today's trading summary from the trade journal.",
            chat_id=chat_id,
        )


if __name__ == "__main__":
    print("Starting Telegram status bot listener...")
    if not settings.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not configured in config/settings.py or .env")

    last_update_id = None
    while True:
        result = get_updates(offset=last_update_id, timeout=30)
        if not result or not result.get("ok"):
            time.sleep(5)
            continue

        for update in result.get("result", []):
            update_id = update["update_id"]
            if last_update_id is None or update_id >= last_update_id:
                last_update_id = update_id + 1

            message = update.get("message") or update.get("edited_message")
            if not message:
                continue

            chat = message.get("chat", {})
            chat_id = str(chat.get("id"))
            text = message.get("text", "")
            if not chat_id or not text:
                continue

            handle_command(chat_id, text)

        time.sleep(1)
