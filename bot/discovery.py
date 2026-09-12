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

_DEFAULT_DISC: dict[str, Any] = {
    "channel_idx": 0,
    "user_query_idx": 0,
    "user_page": 1,
    "repo_query_idx": 0,
    "repo_page": 1,
    "code_query_idx": 0,
    "code_page": 1,
    "repo_queue": [],
    "seen_repos": [],
}


def _discovery_state(settings: Settings) -> dict[str, Any]:
    state = load_state(settings.state_file)
    disc = state.get("discovery")
    if not isinstance(disc, dict):
        disc = dict(_DEFAULT_DISC)
        state["discovery"] = disc
        save_state(state, settings.state_file)
    return disc


def _prune_repo_queue(disc: dict[str, Any]) -> None:
    queue = disc.get("repo_queue") or []
    disc["repo_queue"] = [
        entry
        for entry in queue
        if not (entry.get("stars_done") and entry.get("contrib_done"))
    ]


def _save_discovery(settings: Settings, disc: dict[str, Any]) -> None:
    seen = disc.get("seen_repos") or []
    if len(seen) > 3000:
        disc["seen_repos"] = seen[-2000:]

    _prune_repo_queue(disc)
    queue = disc.get("repo_queue") or []
    if len(queue) > 500:
        disc["repo_queue"] = queue[:500]

    state = load_state(settings.state_file)
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
    if response.status_code == 422:
        logger.warning("GitHub 422 for %s params=%s body=%s", url, params, response.text[:200])
        return None
    if response.status_code == 403:
        logger.warning("GitHub 403 (rate/abuse?) for %s: %s", url, response.text[:200])
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
        "Search %s q=%r page=%s → %s items (total≈%s)",
        endpoint,
        query,
        page,
        len(items),
        data.get("total_count"),
    )
    return items


def _advance_query_cursor(
    disc: dict[str, Any],
    *,
    page_key: str,
    idx_key: str,
    page: int,
    q_idx: int,
    n_queries: int,
    had_items: bool,
    max_page: int = 10,
) -> None:
    if had_items and page < max_page:
        disc[page_key] = page + 1
        return
    disc[page_key] = 1
    disc[idx_key] = (q_idx + 1) % n_queries


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


def _user_stub(item: dict[str, Any]) -> dict[str, Any] | None:
    login = (item.get("login") or "").strip()
    if not login:
        return None
    return {
        "login": login,
        "id": item.get("id"),
        "type": item.get("type") or "User",
        "site_admin": item.get("site_admin", False),
    }


def _users_from_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        stub = _user_stub(item)
        if stub:
            out.append(stub)
    return out


def _owners_from_repos(
    disc: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    from_code_search: bool = False,
) -> list[dict[str, Any]]:
    owners: list[dict[str, Any]] = []
    for item in items:
        repo = (item.get("repository") or {}) if from_code_search else item
        full_name = repo.get("full_name") or ""
        _enqueue_repo(disc, full_name)
        stub = _user_stub(repo.get("owner") or {})
        if stub:
            owners.append(stub)
    return owners


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
    items = _search(session, settings, "users", queries[q_idx], page, need)
    _advance_query_cursor(
        disc,
        page_key="user_page",
        idx_key="user_query_idx",
        page=page,
        q_idx=q_idx,
        n_queries=len(queries),
        had_items=bool(items),
    )
    return _users_from_items(items)[:need]


def _channel_repos(
    session: requests.Session,
    settings: Settings,
    disc: dict[str, Any],
    need: int,
) -> list[dict[str, Any]]:
    queries = settings.cursor_repo_queries or DEFAULT_REPO_QUERIES
    if not queries:
        return []

    q_idx = int(disc.get("repo_query_idx") or 0) % len(queries)
    page = int(disc.get("repo_page") or 1)
    items = _search(session, settings, "repositories", queries[q_idx], page, min(need, 30))
    owners = _owners_from_repos(disc, items)
    _advance_query_cursor(
        disc,
        page_key="repo_page",
        idx_key="repo_query_idx",
        page=page,
        q_idx=q_idx,
        n_queries=len(queries),
        had_items=bool(items),
    )
    return owners[:need]


def _channel_code(
    session: requests.Session,
    settings: Settings,
    disc: dict[str, Any],
    need: int,
) -> list[dict[str, Any]]:
    queries = settings.cursor_code_queries or DEFAULT_CODE_QUERIES
    if not queries:
        return []

    q_idx = int(disc.get("code_query_idx") or 0) % len(queries)
    page = int(disc.get("code_page") or 1)
    items = _search(session, settings, "code", queries[q_idx], page, min(need, 30))
    owners = _owners_from_repos(disc, items, from_code_search=True)
    _advance_query_cursor(
        disc,
        page_key="code_page",
        idx_key="code_query_idx",
        page=page,
        q_idx=q_idx,
        n_queries=len(queries),
        had_items=bool(items),
    )
    return owners[:need]


def _pick_repo(disc: dict[str, Any], *, want_stars: bool) -> dict[str, Any] | None:
    for entry in disc.get("repo_queue") or []:
        if want_stars and not entry.get("stars_done"):
            return entry
        if not want_stars and not entry.get("contrib_done"):
            return entry
    return None


def _paginate_repo_users(
    session: requests.Session,
    settings: Settings,
    entry: dict[str, Any],
    *,
    kind: str,
    need: int,
    max_page: int,
) -> list[dict[str, Any]]:
    full_name = entry["full_name"]
    page_key = "stargazers_page" if kind == "stargazers" else "contributors_page"
    done_key = "stars_done" if kind == "stargazers" else "contrib_done"
    page = int(entry.get(page_key) or 1)
    owner, repo = full_name.split("/", 1)
    url = (
        f"https://api.github.com/repos/{quote_plus(owner)}/{quote_plus(repo)}/{kind}"
    )
    params: dict[str, Any] = {"page": page, "per_page": min(need, 100)}
    if kind == "contributors":
        params["anon"] = "false"

    items = _get_json(session, settings, url, params=params)
    if items is None or not isinstance(items, list):
        entry[done_key] = True
        return []

    logger.info("%s %s page=%s → %s", kind.capitalize(), full_name, page, len(items))
    if not items or page >= max_page:
        entry[done_key] = True
    else:
        entry[page_key] = page + 1

    return _users_from_items(items)[:need]


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
    return _paginate_repo_users(
        session, settings, entry, kind="stargazers", need=need, max_page=20
    )


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
    return _paginate_repo_users(
        session, settings, entry, kind="contributors", need=need, max_page=10
    )


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
    channels = tuple(settings.discovery_channels) or DEFAULT_CHANNELS
    disc = _discovery_state(settings)
    collected: list[str] = []
    seen_logins: set[str] = set()

    with requests.Session() as session:
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

            unique: list[dict[str, Any]] = []
            for user in raw_users:
                login = (user.get("login") or "").strip()
                if not login or login.lower() in seen_logins:
                    continue
                seen_logins.add(login.lower())
                unique.append(user)

            accepted = filter_users(unique, settings, session=session) if unique else []
            collected.extend(accepted)

            disc["channel_idx"] = (idx + 1) % len(channels)
            empty_streak = empty_streak + 1 if not accepted else 0
            _save_discovery(settings, disc)

            if disc["channel_idx"] == started_idx and empty_streak >= len(channels):
                break

    _save_discovery(settings, disc)
    logger.info("Cursor discovery collected %s users this cycle", len(collected))
    return collected[:limit]
