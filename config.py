"""Load runtime settings from environment / .env."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing {name}. Copy .env.example to .env and set your GitHub PAT "
            f"(scopes: read:user, user:follow)."
        )
    return value


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


@dataclass(frozen=True)
class Settings:
    github_token: str
    usernames_file: str
    state_file: str
    fetch_count: int
    follow_delay_min: float
    follow_delay_max: float
    loop_sleep_seconds: int
    max_calls_per_hour: int
    user_agent: str


def load_settings() -> Settings:
    delay_min = _float("FOLLOW_DELAY_MIN", 3.0)
    delay_max = _float("FOLLOW_DELAY_MAX", 6.0)
    if delay_max < delay_min:
        delay_min, delay_max = delay_max, delay_min

    return Settings(
        github_token=_require("GITHUB_TOKEN"),
        usernames_file=os.getenv("USERNAMES_FILE", "usernames.txt").strip() or "usernames.txt",
        state_file=os.getenv("STATE_FILE", "state.json").strip() or "state.json",
        fetch_count=max(1, min(_int("FETCH_COUNT", 100), 100)),
        follow_delay_min=delay_min,
        follow_delay_max=delay_max,
        loop_sleep_seconds=max(30, _int("LOOP_SLEEP_SECONDS", 90)),
        max_calls_per_hour=max(1, _int("MAX_CALLS_PER_HOUR", 1000)),
        user_agent=os.getenv("USER_AGENT", "GitHubFollowerBot").strip() or "GitHubFollowerBot",
    )
