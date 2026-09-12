"""Fetch batches of GitHub users (random stream or Cursor discovery)."""

from __future__ import annotations

import logging

import requests

from bot.config import Settings
from bot.discovery import discover_cursor_users
from bot.filters import filter_users, github_headers
from bot.state_manager import load_state, save_state

logger = logging.getLogger(__name__)


def _fetch_random_users(settings: Settings, count: int) -> list[str]:
    state = load_state(settings.state_file)
    last_fetched_user = int(state.get("last_fetched_user") or 0)

    response = requests.get(
        "https://api.github.com/users",
        params={"per_page": count, "since": last_fetched_user},
        headers=github_headers(settings),
        timeout=30,
    )
    response.raise_for_status()
    fetched = response.json()
    if not fetched:
        return []

    state["last_fetched_user"] = fetched[-1]["id"]
    save_state(state, settings.state_file)

    with requests.Session() as session:
        return filter_users(fetched, settings, session=session)


def fetch_users(settings: Settings, count: int | None = None) -> list[str]:
    limit = count if count is not None else settings.fetch_count

    if settings.user_source == "cursor":
        logger.info("Fetching via Cursor discovery (limit=%s)", limit)
        return discover_cursor_users(settings, limit)

    logger.info("Fetching via random /users stream (limit=%s)", limit)
    return _fetch_random_users(settings, limit)
