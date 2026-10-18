#!/usr/bin/env python3
"""Remove saved browser session and open Playwright for manual Google sign-in.

Usage: python scripts/reset_and_setup_google_login.py

The script deletes `data/session.json`, forces non-headless mode, then
launches the `OlymtradeBot` to let you perform Google Sign-In manually.
On successful login the session will be saved back to `data/session.json`.
"""
import asyncio
import os
from pathlib import Path

from bot.browser import OlymtradeBot, SESSION_FILE
from config import settings


async def main():
    # Delete existing saved session if present
    if os.path.exists(SESSION_FILE):
        try:
            os.remove(SESSION_FILE)
            print(f"Removed existing session file: {SESSION_FILE}")
        except Exception as e:
            print(f"Failed to remove session file: {e}")

    # Force visible browser so user can complete Google OAuth
    try:
        settings.HEADLESS = False
    except Exception:
        pass

    print("Launching browser — please complete Google Sign-In in the opened window.")
    async with OlymtradeBot() as bot:
        ok = await bot.login()
        if ok:
            print(f"Login detected — new session saved to: {SESSION_FILE}")
        else:
            print("Login not detected or timed out. If you signed in, the session may still be saved.")


if __name__ == "__main__":
    asyncio.run(main())
