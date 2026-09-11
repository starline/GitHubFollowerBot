"""GitHub follower bot entrypoint."""

import logging
import random
from pathlib import Path
from time import sleep

import requests
from ratelimit import limits, sleep_and_retry

from config import load_settings
from fetching_new_users import fetching_users_from_github
from state_manager import load_state, save_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("github_follow.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def read_users_from_file() -> list[str]:
    settings = load_settings()
    path = Path(settings.usernames_file)
    if not path.exists():
        path.touch()
        return []

    with path.open("r", encoding="utf-8") as file:
        return [line.strip() for line in file if line.strip()]


def write_users_to_file(users: list[str]) -> None:
    settings = load_settings()
    path = Path(settings.usernames_file)
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
        logger.warning("Last followed user %s not in list; starting from the beginning", last_user)
        return users
    return users[index + 1 :]


def follow_users(users: list[str]) -> None:
    settings = load_settings()

    @sleep_and_retry
    @limits(calls=settings.max_calls_per_hour, period=3600)
    def follow_one(user: str) -> None:
        headers = {
            "Authorization": f"Bearer {settings.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": settings.user_agent,
        }
        url = f"https://api.github.com/user/following/{user}"
        response = requests.put(url, headers=headers, timeout=30)
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
            logger.error("Failed to follow %s: %s %s", user, response.status_code, response.text[:200])

        sleep(random.uniform(settings.follow_delay_min, settings.follow_delay_max))

    for user in users:
        try:
            follow_one(user)
        except requests.exceptions.RequestException as exc:
            logger.error("Request error while following %s: %s", user, exc)


def main() -> None:
    settings = load_settings()
    fetched_users = fetching_users_from_github(settings.fetch_count, settings.github_token)
    logger.info("Fetched %s users", len(fetched_users))
    write_users_to_file(fetched_users)

    users = read_users_from_file()
    last_user = read_last_followed_user()
    logger.info("Last followed user: %s", last_user)

    pending = users_to_follow_from(users, last_user)
    logger.info("Pending follows this cycle: %s", len(pending))
    if pending:
        follow_users(pending)


if __name__ == "__main__":
    while True:
        settings = load_settings()
        try:
            main()
        except Exception as exc:
            logger.exception("Cycle failed: %s", exc)
        logger.info("Sleeping %ss before next cycle", settings.loop_sleep_seconds)
        sleep(settings.loop_sleep_seconds)
