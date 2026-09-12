"""Fetch batches of GitHub users (random stream or Cursor discovery)."""

from __future__ import annotations

import logging

import requests

from bot.config import load_settings
from bot.discovery import discover_cursor_users
from bot.filters import filter_users, github_headers
from bot.state_manager import load_state, save_state

logger = logging.getLogger(__name__)


def _fetch_random_users(count: int, token: str) -> list[str]:
    settings = load_settings()
    state = load_state()
    last_fetched_user = state.get("last_fetched_user") or 0

    url = "https://api.github.com/users"
    params = {
        "per_page": count,
        "since": last_fetched_user,
    }
    headers = github_headers(settings)
    if token != settings.github_token:
        headers = {**headers, "Authorization": f"Bearer {token}"}

    response = requests.get(url, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    fetched_users_api = response.json()

    if not fetched_users_api:
        return []

    state["last_fetched_user"] = fetched_users_api[-1]["id"]
    save_state(state)

    with requests.Session() as session:
        return filter_users(fetched_users_api, settings, session=session)


def fetching_users_from_github(
    users_to_fetch: int | None = None,
    token: str | None = None,
) -> list[str]:
    settings = load_settings()
    count = users_to_fetch or settings.fetch_count
    auth_token = token or settings.github_token

    if settings.user_source == "cursor":
        logger.info("Fetching via Cursor discovery (limit=%s)", count)
        return discover_cursor_users(settings, count)

    logger.info("Fetching via random /users stream (limit=%s)", count)
    return _fetch_random_users(count, auth_token)
