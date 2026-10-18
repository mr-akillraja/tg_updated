import requests
from typing import Optional
from config import settings

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def send_telegram(text: str, chat_id: Optional[str] = None, parse_mode: Optional[str] = None) -> bool:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        return False
    if chat_id is None:
        chat_id = settings.TELEGRAM_CHAT_ID
    if not chat_id:
        return False

    url = TELEGRAM_API.format(token=token, method="sendMessage")
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception:
        return False


def get_updates(offset: Optional[int] = None, timeout: int = 30) -> Optional[dict]:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        return None
    url = TELEGRAM_API.format(token=token, method="getUpdates")
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.get(url, params=params, timeout=timeout + 5)
        return r.json()
    except Exception:
        return None
