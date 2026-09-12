"""Load runtime settings from environment / .env."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_BOT_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _BOT_ROOT / ".env"
_EXAMPLE_PATH = _BOT_ROOT / ".env.example"

_REQUIRED = ("GITHUB_TOKEN",)


def _upsert_env(key: str, value: str) -> None:
    """Create or update KEY=value in .env (preserves other lines/comments)."""
    text = _ENV_PATH.read_text(encoding="utf-8") if _ENV_PATH.exists() else ""
    line = f"{key}={value}"
    pattern = re.compile(rf"^(?:export\s+)?{re.escape(key)}\s*=.*$", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(line, text, count=1)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += line + "\n"
    _ENV_PATH.write_text(text, encoding="utf-8")
    os.environ[key] = value


def _prompt(label: str, *, default: str = "", secret: bool = False) -> str:
    hint = f" [{default}]" if default else ""
    try:
        raw = input(f"{label}{hint}: ").strip()
    except EOFError:
        raw = ""
    if not raw:
        return default
    if secret and raw:
        print("(ok)")
    return raw


def ensure_env_file() -> None:
    """Create .env from the example template if missing."""
    if _ENV_PATH.exists():
        return
    if _EXAMPLE_PATH.exists():
        _ENV_PATH.write_text(_EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        _ENV_PATH.write_text("GITHUB_TOKEN=\n", encoding="utf-8")
    print(f"Created {_ENV_PATH}", file=sys.stderr)


def interactive_setup() -> None:
    """Fill missing required settings interactively when stdin is a TTY."""
    ensure_env_file()
    load_dotenv(_ENV_PATH, override=False)

    missing = [k for k in _REQUIRED if not (os.getenv(k) or "").strip()]
    if not missing:
        return

    if not sys.stdin.isatty():
        raise SystemExit(
            "Missing config: "
            + ", ".join(missing)
            + f". Edit {_ENV_PATH} or run ./run.sh in a terminal."
        )

    print("GitHub Follower Bot — quick setup", file=sys.stderr)
    print("Only required values are asked; the rest use defaults.\n", file=sys.stderr)

    if "GITHUB_TOKEN" in missing:
        token = _prompt(
            "GitHub PAT (scopes: read:user, user:follow)",
            secret=True,
        )
        if not token:
            raise SystemExit("GITHUB_TOKEN is required")
        _upsert_env("GITHUB_TOKEN", token)

    print(f"\nSaved {_ENV_PATH}\n", file=sys.stderr)


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


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    return int(raw)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _csv_lower(name: str) -> tuple[str, ...]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return ()
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


def _csv_pipe(name: str, default: str = "") -> tuple[str, ...]:
    """Pipe-separated values (queries often contain commas)."""
    raw = (os.getenv(name) or default).strip()
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split("|") if part.strip())


def _csv_comma(name: str, default: str = "") -> tuple[str, ...]:
    raw = (os.getenv(name) or default).strip()
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    github_token: str
    data_dir: Path
    usernames_file: Path
    state_file: Path
    log_file: Path
    fetch_count: int
    follow_delay_min: float
    follow_delay_max: float
    loop_sleep_seconds: int
    max_calls_per_hour: int
    user_agent: str
    user_source: str
    bot_mode: str
    discovery_channels: tuple[str, ...]
    cursor_user_queries: tuple[str, ...]
    cursor_repo_queries: tuple[str, ...]
    cursor_code_queries: tuple[str, ...]
    users_only: bool
    skip_site_admin: bool
    skip_bot_logins: bool
    skip_already_following: bool
    filter_on_follow: bool
    min_followers: int | None
    max_followers: int | None
    min_following: int | None
    max_following: int | None
    min_repos: int | None
    max_repos: int | None
    require_bio: bool
    require_location: bool
    require_hireable: bool
    location_contains: tuple[str, ...]
    location_exclude: tuple[str, ...]
    login_exclude: tuple[str, ...]
    skip_dead: bool
    max_inactive_days: int
    require_public_events: bool
    skip_empty_profiles: bool


def load_settings(*, interactive: bool = False) -> Settings:
    """Load settings from .env. Pass interactive=True once at process start."""
    if interactive:
        interactive_setup()
    else:
        ensure_env_file()

    load_dotenv(_ENV_PATH, override=True)

    token = (os.getenv("GITHUB_TOKEN") or "").strip()
    if not token:
        raise SystemExit("GITHUB_TOKEN is required (.env)")

    data_dir = Path(
        os.getenv("BOT_DATA_DIR") or (_BOT_ROOT / "data")
    ).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    delay_min = _float("FOLLOW_DELAY_MIN", 3.0)
    delay_max = _float("FOLLOW_DELAY_MAX", 6.0)
    if delay_max < delay_min:
        delay_min, delay_max = delay_max, delay_min

    def _data_file(raw: str, default_name: str) -> Path:
        if not raw:
            return data_dir / default_name
        path = Path(raw).expanduser()
        if path.is_absolute():
            return path
        return data_dir / path

    user_source = (os.getenv("USER_SOURCE") or "cursor").strip().lower() or "cursor"
    if user_source not in {"cursor", "random"}:
        raise SystemExit("USER_SOURCE must be 'cursor' or 'random'")

    bot_mode = (os.getenv("BOT_MODE") or "both").strip().lower() or "both"
    if bot_mode not in {"both", "discover", "follow"}:
        raise SystemExit("BOT_MODE must be 'both', 'discover', or 'follow'")

    return Settings(
        github_token=token,
        data_dir=data_dir,
        usernames_file=_data_file(
            (os.getenv("USERNAMES_FILE") or "").strip(),
            "usernames.txt",
        ),
        state_file=_data_file((os.getenv("STATE_FILE") or "").strip(), "state.json"),
        log_file=_data_file((os.getenv("LOG_FILE") or "").strip(), "github_follow.log"),
        fetch_count=max(1, min(_int("FETCH_COUNT", 50), 100)),
        follow_delay_min=delay_min,
        follow_delay_max=delay_max,
        loop_sleep_seconds=max(30, _int("LOOP_SLEEP_SECONDS", 90)),
        max_calls_per_hour=max(1, _int("MAX_CALLS_PER_HOUR", 1000)),
        user_agent=os.getenv("USER_AGENT", "GitHubFollowerBot").strip() or "GitHubFollowerBot",
        user_source=user_source,
        bot_mode=bot_mode,
        discovery_channels=_csv_comma(
            "DISCOVERY_CHANNELS",
            "users,repos,stargazers,contributors,code",
        ),
        cursor_user_queries=_csv_pipe("CURSOR_USER_QUERIES"),
        cursor_repo_queries=_csv_pipe("CURSOR_REPO_QUERIES"),
        cursor_code_queries=_csv_pipe("CURSOR_CODE_QUERIES"),
        users_only=_env_bool("FILTER_USERS_ONLY", True),
        skip_site_admin=_env_bool("FILTER_SKIP_SITE_ADMIN", True),
        skip_bot_logins=_env_bool("FILTER_SKIP_BOT_LOGINS", True),
        skip_already_following=_env_bool("FILTER_SKIP_ALREADY_FOLLOWING", True),
        filter_on_follow=_env_bool("FILTER_ON_FOLLOW", True),
        min_followers=_optional_int("MIN_FOLLOWERS"),
        max_followers=_optional_int("MAX_FOLLOWERS"),
        min_following=_optional_int("MIN_FOLLOWING"),
        max_following=_optional_int("MAX_FOLLOWING"),
        min_repos=_optional_int("MIN_REPOS"),
        max_repos=_optional_int("MAX_REPOS"),
        require_bio=_env_bool("REQUIRE_BIO", False),
        require_location=_env_bool("REQUIRE_LOCATION", False),
        require_hireable=_env_bool("REQUIRE_HIREABLE", False),
        location_contains=_csv_lower("LOCATION_CONTAINS"),
        location_exclude=_csv_lower("LOCATION_EXCLUDE"),
        login_exclude=_csv_lower("LOGIN_EXCLUDE"),
        skip_dead=_env_bool("FILTER_SKIP_DEAD", True),
        max_inactive_days=max(1, _int("MAX_INACTIVE_DAYS", 30)),
        require_public_events=_env_bool("FILTER_REQUIRE_PUBLIC_EVENTS", True),
        skip_empty_profiles=_env_bool("FILTER_SKIP_EMPTY_PROFILES", True),
    )
