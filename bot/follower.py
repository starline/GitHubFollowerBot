"""Follow cycle: fetch users, persist queue, follow with rate limits."""

from __future__ import annotations

import logging
import random
from pathlib import Path
from time import sleep

import requests
from ratelimit import limits, sleep_and_retry

from bot.config import load_settings
from bot.fetching_new_users import fetching_users_from_github
from bot.filters import github_headers, login_passes
from bot.state_manager import load_state, save_state

logger = logging.getLogger(__name__)


def read_users_from_file() -> list[str]:
    settings = load_settings()
    path = Path(settings.usernames_file)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return []

    with path.open("r", encoding="utf-8") as file:
        return [line.strip() for line in file if line.strip()]


def write_users_to_file(users: list[str]) -> None:
    settings = load_settings()
    path = Path(settings.usernames_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = set(read_users_from_file())
    new_users = [user for user in users if user not in existing]
    if not new_users:
        return

    with path.open("a", encoding="utf-8") as file:
        for user in new_users:
            file.write(f"{user}\n")


def read_last_followed_user() -> str | None:
    return load_state().get("last_followed_user")


def write_last_followed_user(user: str) -> None:
    state = load_state()
    state["last_followed_user"] = user
    save_state(state)


def bump_follow_counter() -> None:
    state = load_state()
    state["how_many_bot_followed_so_far_counter"] = (
        state.get("how_many_bot_followed_so_far_counter", 0) + 1
    )
    save_state(state)


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


def follow_users(users: list[str]) -> None:
    settings = load_settings()

    @sleep_and_retry
    @limits(calls=settings.max_calls_per_hour, period=3600)
    def follow_one(user: str, session: requests.Session) -> None:
        if settings.filter_on_follow and not login_passes(user, settings, session=session):
            write_last_followed_user(user)
            return

        url = f"https://api.github.com/user/following/{user}"
        response = session.put(url, headers=github_headers(settings), timeout=30)
        write_last_followed_user(user)
        bump_follow_counter()

        if response.status_code == 204:
            logger.info("Followed %s", user)
        elif response.status_code == 404:
            logger.warning("User %s not found", user)
        elif response.status_code == 429:
            logger.warning("Rate limited while following %s; sleeping 100s", user)
            sleep(100)
        else:
            logger.error(
                "Failed to follow %s: %s %s",
                user,
                response.status_code,
                response.text[:200],
            )

        sleep(random.uniform(settings.follow_delay_min, settings.follow_delay_max))

    with requests.Session() as session:
        for user in users:
            try:
                follow_one(user, session)
            except requests.exceptions.RequestException as exc:
                logger.error("Request error while following %s: %s", user, exc)


def run_cycle() -> None:
    settings = load_settings()

    if settings.bot_mode in {"both", "discover"}:
        fetched_users = fetching_users_from_github(
            settings.fetch_count,
            settings.github_token,
        )
        logger.info("Fetched %s users (source=%s)", len(fetched_users), settings.user_source)
        write_users_to_file(fetched_users)
    else:
        logger.info("BOT_MODE=follow — skipping discovery")

    if settings.bot_mode == "discover":
        logger.info("BOT_MODE=discover — skipping follow")
        return

    users = read_users_from_file()
    last_user = read_last_followed_user()
    logger.info("Last followed user: %s", last_user)

    pending = users_to_follow_from(users, last_user)
    logger.info("Pending follows this cycle: %s", len(pending))
    if pending:
        follow_users(pending)
