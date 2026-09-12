"""Criteria-based GitHub user search — core of each bot cycle when USER_SOURCE=cursor."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import requests

from bot.config import Settings
from bot.filters import filter_users, github_headers
from bot.state_manager import load_state, save_state

logger = logging.getLogger(__name__)

DEFAULT_USER_QUERIES = (
    "cursor in:bio",
    '"cursor ide" in:bio',
    '"cursor.sh" in:bio',
    "cursor.ai in:bio",
    "anysphere in:bio",
)

DEFAULT_REPO_QUERIES = (
    "cursor ide in:name,description,topics",
    "topic:cursor",
    "topic:cursor-ai",
    "cursor.ai in:name,description",
    ".cursorrules in:name",
    '"cursor rules" in:description',
)

DEFAULT_CODE_QUERIES = (
    "filename:.cursorrules",
    "path:.cursor/rules",
    "filename:AGENTS.md cursor",
)

DEFAULT_CHANNELS = ("users", "repos", "stargazers", "contributors", "code")


def _disc(settings: Settings) -> dict[str, Any]:
    state = load_state(settings.state_file)
    return state["discovery"]


def _save_disc(settings: Settings, disc: dict[str, Any]) -> None:
    state = load_state(settings.state_file)
    seen = disc.get("seen_repos") or []
    if len(seen) > 3000:
        disc["seen_repos"] = seen[-2000:]
    queue = disc.get("repo_queue") or []
    if len(queue) > 500:
        disc["repo_queue"] = queue[:500]
    state["discovery"] = disc
    save_state(state, settings.state_file)


def _get_json(
    session: requests.Session,
    settings: Settings,
    url: str,
    *,
    params: dict[str, Any] | None = None,
) -> Any:
    response = session.get(
        url,
        headers=github_headers(settings),
        params=params,
        timeout=45,
    )
    if response.status_code in {403, 404, 422}:
        logger.warning(
            "GitHub %s for %s: %s",
            response.status_code,
            url,
            response.text[:200],
        )
        return None
    response.raise_for_status()
    return response.json()


def _search(
    session: requests.Session,
    settings: Settings,
    endpoint: str,
    query: str,
    page: int,
    per_page: int,
) -> list[dict[str, Any]]:
    data = _get_json(
        session,
        settings,
        f"https://api.github.com/search/{endpoint}",
        params={"q": query, "page": page, "per_page": min(per_page, 100)},
    )
    if not data:
        return []
    items = data.get("items") or []
    logger.info(
        "Search %s q=%r page=%s → %s (total≈%s)",
        endpoint,
        query,
        page,
        len(items),
        data.get("total_count"),
    )
    return items


def _as_user(item: dict[str, Any]) -> dict[str, Any] | None:
    login = (item.get("login") or "").strip()
    if not login:
        return None
    return {
        "login": login,
        "id": item.get("id"),
        "type": item.get("type") or "User",
        "site_admin": item.get("site_admin", False),
    }


def _enqueue_repo(disc: dict[str, Any], full_name: str) -> None:
    if not full_name or "/" not in full_name:
        return
    seen: list[str] = disc.setdefault("seen_repos", [])
    if full_name in seen:
        return
    seen.append(full_name)
    disc.setdefault("repo_queue", []).append(
        {
            "full_name": full_name,
            "stargazers_page": 1,
            "contributors_page": 1,
            "stars_done": False,
            "contrib_done": False,
        }
    )
    logger.info("Queued repo %s", full_name)


def _channel_users(
    session: requests.Session,
    settings: Settings,
    disc: dict[str, Any],
    need: int,
) -> list[dict[str, Any]]:
    queries = settings.cursor_user_queries or DEFAULT_USER_QUERIES
    q_idx = int(disc.get("user_query_idx") or 0) % len(queries)
    page = int(disc.get("user_page") or 1)
    items = _search(session, settings, "users", queries[q_idx], page, need)

    if items:
        disc["user_page"] = page + 1
        if page >= 10:
            disc["user_page"] = 1
            disc["user_query_idx"] = (q_idx + 1) % len(queries)
    else:
        disc["user_page"] = 1
        disc["user_query_idx"] = (q_idx + 1) % len(queries)

    out: list[dict[str, Any]] = []
    for item in items:
        user = _as_user(item)
        if user:
            out.append(user)
    return out[:need]


def _owners_from_repos(
    session: requests.Session,
    settings: Settings,
    disc: dict[str, Any],
    need: int,
    *,
    queries: tuple[str, ...],
    q_key: str,
    page_key: str,
    endpoint: str,
) -> list[dict[str, Any]]:
    if not queries:
        return []
    q_idx = int(disc.get(q_key) or 0) % len(queries)
    page = int(disc.get(page_key) or 1)
    items = _search(session, settings, endpoint, queries[q_idx], page, min(need, 30))

    owners: list[dict[str, Any]] = []
    for item in items:
        repo = item if endpoint == "repositories" else (item.get("repository") or {})
        full_name = repo.get("full_name") or ""
        _enqueue_repo(disc, full_name)
        owner = repo.get("owner") or {}
        user = _as_user(owner)
        if user:
            owners.append(user)

    if items:
        disc[page_key] = page + 1
        if page >= 10:
            disc[page_key] = 1
            disc[q_key] = (q_idx + 1) % len(queries)
    else:
        disc[page_key] = 1
        disc[q_key] = (q_idx + 1) % len(queries)

    return owners[:need]


def _channel_repos(
    session: requests.Session,
    settings: Settings,
    disc: dict[str, Any],
    need: int,
) -> list[dict[str, Any]]:
    return _owners_from_repos(
        session,
        settings,
        disc,
        need,
        queries=settings.cursor_repo_queries or DEFAULT_REPO_QUERIES,
        q_key="repo_query_idx",
        page_key="repo_page",
        endpoint="repositories",
    )


def _channel_code(
    session: requests.Session,
    settings: Settings,
    disc: dict[str, Any],
    need: int,
) -> list[dict[str, Any]]:
    return _owners_from_repos(
        session,
        settings,
        disc,
        need,
        queries=settings.cursor_code_queries or DEFAULT_CODE_QUERIES,
        q_key="code_query_idx",
        page_key="code_page",
        endpoint="code",
    )


def _pick_repo(disc: dict[str, Any], *, want_stars: bool) -> dict[str, Any] | None:
    for entry in disc.get("repo_queue") or []:
        if want_stars and not entry.get("stars_done"):
            return entry
        if not want_stars and not entry.get("contrib_done"):
            return entry
    return None


def _channel_stargazers(
    session: requests.Session,
    settings: Settings,
    disc: dict[str, Any],
    need: int,
) -> list[dict[str, Any]]:
    entry = _pick_repo(disc, want_stars=True)
    if entry is None:
        logger.info("No repos queued for stargazers yet")
        return []

    full_name = entry["full_name"]
    page = int(entry.get("stargazers_page") or 1)
    owner, repo = full_name.split("/", 1)
    url = (
        f"https://api.github.com/repos/{quote(owner, safe='')}/"
        f"{quote(repo, safe='')}/stargazers"
    )
    items = _get_json(
        session,
        settings,
        url,
        params={"page": page, "per_page": min(need, 100)},
    )
    if not isinstance(items, list):
        entry["stars_done"] = True
        return []

    logger.info("Stargazers %s page=%s → %s", full_name, page, len(items))
    if not items or page >= 20:
        entry["stars_done"] = True
    else:
        entry["stargazers_page"] = page + 1

    out: list[dict[str, Any]] = []
    for item in items:
        user = _as_user(item)
        if user:
            out.append(user)
    return out[:need]


def _channel_contributors(
    session: requests.Session,
    settings: Settings,
    disc: dict[str, Any],
    need: int,
) -> list[dict[str, Any]]:
    entry = _pick_repo(disc, want_stars=False)
    if entry is None:
        logger.info("No repos queued for contributors yet")
        return []

    full_name = entry["full_name"]
    page = int(entry.get("contributors_page") or 1)
    owner, repo = full_name.split("/", 1)
    url = (
        f"https://api.github.com/repos/{quote(owner, safe='')}/"
        f"{quote(repo, safe='')}/contributors"
    )
    items = _get_json(
        session,
        settings,
        url,
        params={"page": page, "per_page": min(need, 100), "anon": "false"},
    )
    if not isinstance(items, list):
        entry["contrib_done"] = True
        return []

    logger.info("Contributors %s page=%s → %s", full_name, page, len(items))
    if not items or page >= 10:
        entry["contrib_done"] = True
    else:
        entry["contributors_page"] = page + 1

    out: list[dict[str, Any]] = []
    for item in items:
        user = _as_user(item)
        if user:
            out.append(user)
    return out[:need]


_HANDLERS = {
    "users": _channel_users,
    "repos": _channel_repos,
    "stargazers": _channel_stargazers,
    "contributors": _channel_contributors,
    "code": _channel_code,
}


def search_users_by_criteria(settings: Settings, limit: int) -> list[str]:
    """
    Each bot cycle: search GitHub by configured criteria, filter, return logins.

    Channels (rotated, progress in state.json → discovery):
      users / repos / stargazers / contributors / code
    """
    channels = settings.discovery_channels or DEFAULT_CHANNELS
    disc = _disc(settings)
    collected: list[str] = []
    seen: set[str] = set()

    with requests.Session() as session:
        empty_streak = 0
        while len(collected) < limit and empty_streak < len(channels) * 2:
            idx = int(disc.get("channel_idx") or 0) % len(channels)
            channel = channels[idx]
            handler = _HANDLERS.get(channel)
            need = limit - len(collected)
            logger.info("Search channel=%s need=%s", channel, need)

            raw: list[dict[str, Any]] = []
            if handler:
                try:
                    raw = handler(session, settings, disc, need)
                except requests.exceptions.RequestException as exc:
                    logger.error("Search channel %s failed: %s", channel, exc)

            unique: list[dict[str, Any]] = []
            for user in raw:
                login = (user.get("login") or "").strip()
                key = login.lower()
                if not login or key in seen:
                    continue
                seen.add(key)
                unique.append(user)

            accepted = filter_users(unique, settings, session=session) if unique else []
            collected.extend(accepted)

            disc["channel_idx"] = (idx + 1) % len(channels)
            empty_streak = empty_streak + 1 if not accepted else 0
            _save_disc(settings, disc)

    _save_disc(settings, disc)
    logger.info("Criteria search collected %s users", len(collected))
    return collected[:limit]
