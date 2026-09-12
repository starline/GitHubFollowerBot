"""User eligibility filters for fetch/follow."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests

from bot.config import Settings

logger = logging.getLogger(__name__)


def github_headers(settings: Settings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": settings.user_agent,
    }


def _parse_gh_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _days_ago(when: datetime) -> float:
    now = datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (now - when).total_seconds() / 86400.0


def _needs_profile(settings: Settings) -> bool:
    return any(
        (
            settings.skip_dead,
            settings.min_followers is not None,
            settings.max_followers is not None,
            settings.min_following is not None,
            settings.max_following is not None,
            settings.min_repos is not None,
            settings.max_repos is not None,
            settings.require_bio,
            settings.require_location,
            bool(settings.location_contains),
            bool(settings.location_exclude),
            settings.require_hireable,
        )
    )


def _check_list_fields(user: dict[str, Any], settings: Settings) -> str | None:
    """Return skip reason or None if OK (list/search payload)."""
    login = (user.get("login") or "").strip()
    if not login:
        return "empty login"

    login_l = login.lower()
    if login_l in settings.login_exclude:
        return "login excluded"

    if settings.skip_bot_logins and (
        login_l.endswith("[bot]") or login_l.endswith("-bot") or login_l.startswith("bot-")
    ):
        return "bot-like login"

    user_type = (user.get("type") or "User").strip()
    if settings.users_only and user_type != "User":
        return f"type={user_type}"

    if settings.skip_site_admin and user.get("site_admin"):
        return "site_admin"

    return None


def _check_profile(profile: dict[str, Any], settings: Settings) -> str | None:
    """Return skip reason or None if OK (full /users/{login} payload)."""
    reason = _check_list_fields(profile, settings)
    if reason:
        return reason

    followers = int(profile.get("followers") or 0)
    following = int(profile.get("following") or 0)
    repos = int(profile.get("public_repos") or 0)

    if settings.min_followers is not None and followers < settings.min_followers:
        return f"followers={followers} < {settings.min_followers}"
    if settings.max_followers is not None and followers > settings.max_followers:
        return f"followers={followers} > {settings.max_followers}"

    if settings.min_following is not None and following < settings.min_following:
        return f"following={following} < {settings.min_following}"
    if settings.max_following is not None and following > settings.max_following:
        return f"following={following} > {settings.max_following}"

    if settings.min_repos is not None and repos < settings.min_repos:
        return f"repos={repos} < {settings.min_repos}"
    if settings.max_repos is not None and repos > settings.max_repos:
        return f"repos={repos} > {settings.max_repos}"

    bio = (profile.get("bio") or "").strip()
    if settings.require_bio and not bio:
        return "no bio"

    location = (profile.get("location") or "").strip()
    location_l = location.lower()
    if settings.require_location and not location:
        return "no location"

    if settings.location_contains and not any(s in location_l for s in settings.location_contains):
        return f"location miss ({location or 'empty'})"

    if settings.location_exclude and any(s in location_l for s in settings.location_exclude):
        return f"location excluded ({location})"

    if settings.require_hireable and not profile.get("hireable"):
        return "not hireable"

    if settings.skip_dead:
        if settings.skip_empty_profiles and repos == 0 and followers == 0:
            return "empty shell (0 repos, 0 followers)"

        updated = _parse_gh_time(profile.get("updated_at"))
        if updated is not None:
            age = _days_ago(updated)
            if age > settings.max_inactive_days:
                return f"stale profile ({age:.0f}d > {settings.max_inactive_days}d)"

    return None


def fetch_profile(login: str, settings: Settings, session: requests.Session | None = None) -> dict[str, Any] | None:
    http = session or requests
    url = f"https://api.github.com/users/{login}"
    response = http.get(url, headers=github_headers(settings), timeout=30)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def fetch_latest_public_event(
    login: str,
    settings: Settings,
    session: requests.Session | None = None,
) -> dict[str, Any] | None:
    http = session or requests
    url = f"https://api.github.com/users/{login}/events/public"
    response = http.get(
        url,
        headers=github_headers(settings),
        params={"per_page": 1},
        timeout=30,
    )
    if response.status_code in {404, 422}:
        return None
    response.raise_for_status()
    items = response.json()
    if not isinstance(items, list) or not items:
        return None
    return items[0]


def _check_recent_activity(
    login: str,
    settings: Settings,
    session: requests.Session | None = None,
) -> str | None:
    """Extra dead-user check via public events (costs 1 API call)."""
    if not settings.skip_dead or not settings.require_public_events:
        return None

    event = fetch_latest_public_event(login, settings, session)
    if event is None:
        return "no public events"

    created = _parse_gh_time(event.get("created_at"))
    if created is None:
        return "no public events"

    age = _days_ago(created)
    if age > settings.max_inactive_days:
        return f"stale activity ({age:.0f}d > {settings.max_inactive_days}d)"
    return None


def is_already_following(login: str, settings: Settings, session: requests.Session | None = None) -> bool:
    http = session or requests
    url = f"https://api.github.com/user/following/{login}"
    response = http.get(url, headers=github_headers(settings), timeout=30)
    if response.status_code == 204:
        return True
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return False


def evaluate_user(
    user: dict[str, Any],
    settings: Settings,
    *,
    session: requests.Session | None = None,
    profile: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """
    Evaluate a user dict from the list API (and optionally a full profile).
    Returns (accepted, reason). reason is 'ok' when accepted.
    """
    reason = _check_list_fields(user, settings)
    if reason:
        return False, reason

    login = user["login"]

    if settings.skip_already_following and is_already_following(login, settings, session):
        return False, "already following"

    if not _needs_profile(settings):
        return True, "ok"

    full = profile
    if full is None:
        full = fetch_profile(login, settings, session)
    if full is None:
        return False, "not found / dead"

    reason = _check_profile(full, settings)
    if reason:
        return False, reason

    reason = _check_recent_activity(login, settings, session)
    if reason:
        return False, reason

    return True, "ok"


def filter_users(
    users: list[dict[str, Any]],
    settings: Settings,
    *,
    session: requests.Session | None = None,
) -> list[str]:
    """Return logins that pass filters."""
    accepted: list[str] = []
    for user in users:
        login = (user.get("login") or "").strip()
        if not login:
            continue
        ok, reason = evaluate_user(user, settings, session=session)
        if ok:
            accepted.append(login)
            logger.info("Accept %s", login)
        else:
            logger.info("Skip %s (%s)", login, reason)
    return accepted


def login_passes(
    login: str,
    settings: Settings,
    *,
    session: requests.Session | None = None,
) -> bool:
    """Re-check a queued login via full profile."""
    profile = fetch_profile(login, settings, session)
    if profile is None:
        logger.info("Skip %s (not found)", login)
        return False
    ok, reason = evaluate_user(profile, settings, session=session, profile=profile)
    if not ok:
        logger.info("Skip %s (%s)", login, reason)
    return ok
