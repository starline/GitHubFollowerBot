"""Follow cycle: fetch users, persist queue, follow with rate limits."""

from __future__ import annotations

import logging
import random
from pathlib import Path
from time import sleep

import requests
from ratelimit import limits, sleep_and_retry

from bot.config import Settings
from bot.fetching_new_users import fetch_users
from bot.filters import github_headers, login_passes
from bot.state_manager import load_state, save_state

logger = logging.getLogger(__name__)


def read_users_from_file(path: Path) -> list[str]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return []

    with path.open("r", encoding="utf-8") as file:
        return [line.strip() for line in file if line.strip()]


def append_users_to_file(path: Path, users: list[str]) -> int:
    """Append new usernames; returns how many were added."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = set(read_users_from_file(path))
    new_users = [user for user in users if user not in existing]
    if not new_users:
        return 0

    with path.open("a", encoding="utf-8") as file:
        for user in new_users:
            file.write(f"{user}\n")
    return len(new_users)


def _mark_followed(settings: Settings, user: str, *, bump: bool = False) -> None:
    state = load_state(settings.state_file)
    state["last_followed_user"] = user
    if bump:
        state["how_many_bot_followed_so_far_counter"] = (
            int(state.get("how_many_bot_followed_so_far_counter") or 0) + 1
        )
    save_state(state, settings.state_file)


def users_to_follow_from(users: list[str], last_user: str | None) -> list[str]:
    if not last_user:
        return users
    try:
        index = users.index(last_user)
    except ValueError:
        logger.warning(
            "Last followed user %s not in list; starting from the beginning",
            last_user,
        )
        return users
    return users[index + 1 :]


def follow_users(settings: Settings, users: list[str]) -> None:
    @sleep_and_retry
    @limits(calls=settings.max_calls_per_hour, period=3600)
    def follow_one(user: str, session: requests.Session) -> None:
        if settings.filter_on_follow and not login_passes(user, settings, session=session):
            _mark_followed(settings, user)
            return

        url = f"https://api.github.com/user/following/{user}"
        response = session.put(url, headers=github_headers(settings), timeout=30)

        if response.status_code == 204:
            _mark_followed(settings, user, bump=True)
            logger.info("Followed %s", user)
        elif response.status_code == 404:
            _mark_followed(settings, user)
            logger.warning("User %s not found", user)
        elif response.status_code == 429:
            logger.warning("Rate limited while following %s; sleeping 100s", user)
            sleep(100)
            return
        else:
            logger.error(
                "Failed to follow %s: %s %s",
                user,
                response.status_code,
                response.text[:200],
            )
            return

        sleep(random.uniform(settings.follow_delay_min, settings.follow_delay_max))

    with requests.Session() as session:
        for user in users:
            try:
                follow_one(user, session)
            except requests.exceptions.RequestException as exc:
                logger.error("Request error while following %s: %s", user, exc)


def run_cycle(settings: Settings) -> None:
    if settings.bot_mode in {"both", "discover"}:
        fetched_users = fetch_users(settings)
        logger.info("Fetched %s users (source=%s)", len(fetched_users), settings.user_source)
        added = append_users_to_file(settings.usernames_file, fetched_users)
        if added:
            logger.info("Appended %s new usernames to queue", added)
    else:
        logger.info("BOT_MODE=follow — skipping discovery")

    if settings.bot_mode == "discover":
        logger.info("BOT_MODE=discover — skipping follow")
        return

    users = read_users_from_file(settings.usernames_file)
    state = load_state(settings.state_file)
    last_user = state.get("last_followed_user")
    logger.info("Last followed user: %s", last_user)

    pending = users_to_follow_from(users, last_user)
    logger.info("Pending follows this cycle: %s", len(pending))
    if pending:
        follow_users(settings, pending)
