"""Discover GitHub users interested in / building with Cursor."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote_plus

import requests

from bot.config import Settings
from bot.filters import filter_users, github_headers
from bot.state_manager import load_state, save_state

logger = logging.getLogger(__name__)

# Search API is stricter (~30 req/min authenticated).
_SEARCH_ACCEPT = "application/vnd.github+json"

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

DEFAULT_DISCOVERY_STATE: dict[str, Any] = {
    "channel_idx": 0,
    "user_query_idx": 0,
    "user_page": 1,
    "repo_query_idx": 0,
    "repo_page": 1,
    "code_query_idx": 0,
    "code_page": 1,
    "repo_queue": [],  # [{full_name, stargazers_page, contributors_page, stars_done, contrib_done}]
    "seen_repos": [],
}


def _discovery_state() -> dict[str, Any]:
    state = load_state()
    disc = state.get("discovery")
    if not isinstance(disc, dict):
        disc = DEFAULT_DISCOVERY_STATE.copy()
        state["discovery"] = disc
        save_state(state)
        return disc

    merged = DEFAULT_DISCOVERY_STATE.copy()
    merged.update(disc)
    if not isinstance(merged.get("repo_queue"), list):
        merged["repo_queue"] = []
    if not isinstance(merged.get("seen_repos"), list):
        merged["seen_repos"] = []
    state["discovery"] = merged
    return merged


def _save_discovery(disc: dict[str, Any]) -> None:
    state = load_state()
    # Cap seen_repos growth
    seen = disc.get("seen_repos") or []
    if len(seen) > 3000:
        disc["seen_repos"] = seen[-2000:]
    # Cap queue
    queue = disc.get("repo_queue") or []
    if len(queue) > 500:
        disc["repo_queue"] = queue[:500]
    state["discovery"] = disc
    save_state(state)


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
    if response.status_code == 422:
        logger.warning("GitHub 422 for %s params=%s body=%s", url, params, response.text[:200])
        return None
    if response.status_code == 403:
        logger.warning(
            "GitHub 403 (rate/abuse?) for %s: %s",
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
    url = f"https://api.github.com/search/{endpoint}"
    data = _get_json(
        session,
        settings,
        url,
        params={"q": query, "page": page, "per_page": min(per_page, 100)},
    )
    if not data:
        return []
    items = data.get("items") or []
    logger.info(
        "Search %s q=%r page=%s → %s items (total≈%s)",
        endpoint,
        query,
        page,
        len(items),
        data.get("total_count"),
    )
    return items


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
    logger.info("Queued repo %s for mining", full_name)


def _users_from_search_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        login = (item.get("login") or "").strip()
        if not login:
            continue
        out.append(
            {
                "login": login,
                "id": item.get("id"),
                "type": item.get("type") or "User",
                "site_admin": item.get("site_admin", False),
            }
        )
    return out


def _channel_users(
    session: requests.Session,
    settings: Settings,
    disc: dict[str, Any],
    need: int,
) -> list[dict[str, Any]]:
    queries = settings.cursor_user_queries or DEFAULT_USER_QUERIES
    if not queries:
        return []

    q_idx = int(disc.get("user_query_idx") or 0) % len(queries)
    page = int(disc.get("user_page") or 1)
    query = queries[q_idx]
    items = _search(session, settings, "users", query, page, need)
    users = _users_from_search_items(items)

    if items:
        disc["user_page"] = page + 1
        # GitHub search hard-caps at 1000 results (page*per_page)
        if page >= 10:
            disc["user_page"] = 1
            disc["user_query_idx"] = (q_idx + 1) % len(queries)
    else:
        disc["user_page"] = 1
        disc["user_query_idx"] = (q_idx + 1) % len(queries)

    return users[:need]


def _channel_repos(
    session: requests.Session,
    settings: Settings,
    disc: dict[str, Any],
    need: int,
) -> list[dict[str, Any]]:
    """Find Cursor-related repos; enqueue for stargazer/contributor mining; return owners."""
    queries = settings.cursor_repo_queries or DEFAULT_REPO_QUERIES
    if not queries:
        return []

    q_idx = int(disc.get("repo_query_idx") or 0) % len(queries)
    page = int(disc.get("repo_page") or 1)
    query = queries[q_idx]
    items = _search(session, settings, "repositories", query, page, min(need, 30))

    owners: list[dict[str, Any]] = []
    for repo in items:
        full_name = repo.get("full_name") or ""
        _enqueue_repo(disc, full_name)
        owner = repo.get("owner") or {}
        login = (owner.get("login") or "").strip()
        if login:
            owners.append(
                {
                    "login": login,
                    "id": owner.get("id"),
                    "type": owner.get("type") or "User",
                    "site_admin": owner.get("site_admin", False),
                }
            )

    if items:
        disc["repo_page"] = page + 1
        if page >= 10:
            disc["repo_page"] = 1
            disc["repo_query_idx"] = (q_idx + 1) % len(queries)
    else:
        disc["repo_page"] = 1
        disc["repo_query_idx"] = (q_idx + 1) % len(queries)

    return owners[:need]


def _channel_code(
    session: requests.Session,
    settings: Settings,
    disc: dict[str, Any],
    need: int,
) -> list[dict[str, Any]]:
    """Users who commit Cursor config files (.cursorrules, .cursor/rules)."""
    queries = settings.cursor_code_queries or DEFAULT_CODE_QUERIES
    if not queries:
        return []

    q_idx = int(disc.get("code_query_idx") or 0) % len(queries)
    page = int(disc.get("code_page") or 1)
    query = queries[q_idx]
    items = _search(session, settings, "code", query, page, min(need, 30))

    owners: list[dict[str, Any]] = []
    for item in items:
        repo = item.get("repository") or {}
        full_name = repo.get("full_name") or ""
        _enqueue_repo(disc, full_name)
        owner = repo.get("owner") or {}
        login = (owner.get("login") or "").strip()
        if login:
            owners.append(
                {
                    "login": login,
                    "id": owner.get("id"),
                    "type": owner.get("type") or "User",
                    "site_admin": owner.get("site_admin", False),
                }
            )

    if items:
        disc["code_page"] = page + 1
        if page >= 10:
            disc["code_page"] = 1
            disc["code_query_idx"] = (q_idx + 1) % len(queries)
    else:
        disc["code_page"] = 1
        disc["code_query_idx"] = (q_idx + 1) % len(queries)

    return owners[:need]


def _pick_repo(disc: dict[str, Any], *, want_stars: bool) -> dict[str, Any] | None:
    queue: list[dict[str, Any]] = disc.get("repo_queue") or []
    for entry in queue:
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
    url = f"https://api.github.com/repos/{quote_plus(owner)}/{quote_plus(repo)}/stargazers"
    items = _get_json(
        session,
        settings,
        url,
        params={"page": page, "per_page": min(need, 100)},
    )
    if items is None:
        entry["stars_done"] = True
        return []
    if not isinstance(items, list):
        entry["stars_done"] = True
        return []

    logger.info("Stargazers %s page=%s → %s", full_name, page, len(items))
    if not items:
        entry["stars_done"] = True
    else:
        entry["stargazers_page"] = page + 1
        if page >= 20:
            entry["stars_done"] = True

    return _users_from_search_items(items)[:need]


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
    url = f"https://api.github.com/repos/{quote_plus(owner)}/{quote_plus(repo)}/contributors"
    items = _get_json(
        session,
        settings,
        url,
        params={"page": page, "per_page": min(need, 100), "anon": "false"},
    )
    if items is None:
        entry["contrib_done"] = True
        return []
    if not isinstance(items, list):
        entry["contrib_done"] = True
        return []

    logger.info("Contributors %s page=%s → %s", full_name, page, len(items))
    if not items:
        entry["contrib_done"] = True
    else:
        entry["contributors_page"] = page + 1
        if page >= 10:
            entry["contrib_done"] = True

    return _users_from_search_items(items)[:need]


_CHANNEL_HANDLERS = {
    "users": _channel_users,
    "repos": _channel_repos,
    "stargazers": _channel_stargazers,
    "contributors": _channel_contributors,
    "code": _channel_code,
}


def discover_cursor_users(settings: Settings, limit: int) -> list[str]:
    """
    Collect up to `limit` Cursor-related user logins (after filters).

    Sources (rotated via state):
      users        — Search API: cursor in bio
      repos        — Cursor-related repos → owners (+ enqueue for mining)
      stargazers   — people who starred those repos
      contributors — people who commit to those repos
      code         — owners of repos containing .cursorrules / .cursor/rules
    """
    channels = settings.discovery_channels or DEFAULT_CHANNELS
    if not channels:
        channels = DEFAULT_CHANNELS

    disc = _discovery_state()
    collected: list[str] = []
    seen_logins: set[str] = set()

    with requests.Session() as session:
        # Prefer search media type for search endpoints (headers already fine).
        session.headers.update({"Accept": _SEARCH_ACCEPT})

        started_idx = int(disc.get("channel_idx") or 0) % len(channels)
        empty_streak = 0

        while len(collected) < limit and empty_streak < len(channels) * 2:
            idx = int(disc.get("channel_idx") or 0) % len(channels)
            channel = channels[idx]
            handler = _CHANNEL_HANDLERS.get(channel)
            need = limit - len(collected)
            logger.info("Discovery channel=%s need=%s", channel, need)

            raw_users: list[dict[str, Any]] = []
            if handler:
                try:
                    raw_users = handler(session, settings, disc, need)
                except requests.exceptions.RequestException as exc:
                    logger.error("Discovery channel %s failed: %s", channel, exc)
                    raw_users = []

            # Drop duplicates within this batch / cycle
            unique = []
            for user in raw_users:
                login = (user.get("login") or "").strip()
                if not login or login.lower() in seen_logins:
                    continue
                seen_logins.add(login.lower())
                unique.append(user)

            accepted = filter_users(unique, settings, session=session) if unique else []
            collected.extend(accepted)

            disc["channel_idx"] = (idx + 1) % len(channels)
            if not accepted:
                empty_streak += 1
            else:
                empty_streak = 0

            # Persist progress between channel hops
            _save_discovery(disc)

            # Avoid infinite spin if we wrapped without filling quota
            if disc["channel_idx"] == started_idx and empty_streak >= len(channels):
                break

    _save_discovery(disc)
    logger.info("Cursor discovery collected %s users this cycle", len(collected))
    return collected[:limit]
