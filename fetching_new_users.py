"""Fetch batches of GitHub users via the public users API."""

from typing import List

import requests

from config import load_settings
from state_manager import load_state, save_state


def fetching_users_from_github(users_to_fetch: int | None = None, token: str | None = None) -> List[str]:
    settings = load_settings()
    count = users_to_fetch or settings.fetch_count
    auth_token = token or settings.github_token

    state = load_state()
    last_fetched_user = state.get("last_fetched_user") or 0

    url = "https://api.github.com/users"
    params = {
        "per_page": count,
        "since": last_fetched_user,
    }
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": settings.user_agent,
    }

    response = requests.get(url, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    fetched_users_api = response.json()

    if not fetched_users_api:
        return []

    fetched_users = [user["login"] for user in fetched_users_api]
    state["last_fetched_user"] = fetched_users_api[-1]["id"]
    save_state(state)
    return fetched_users
