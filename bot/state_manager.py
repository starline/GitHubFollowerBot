"""Persist bot progress in state.json."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

DEFAULT_STATE: dict[str, Any] = {
    "last_followed_user": None,
    "how_many_bot_followed_so_far_counter": 0,
    "last_fetched_user": 0,
    "discovery": {
        "channel_idx": 0,
        "user_query_idx": 0,
        "user_page": 1,
        "repo_query_idx": 0,
        "repo_page": 1,
        "code_query_idx": 0,
        "code_page": 1,
        "repo_queue": [],
        "seen_repos": [],
    },
}


def _merge_state(raw: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(DEFAULT_STATE)
    for key, value in raw.items():
        if key == "discovery" and isinstance(value, dict):
            disc = deepcopy(DEFAULT_STATE["discovery"])
            disc.update(value)
            if not isinstance(disc.get("repo_queue"), list):
                disc["repo_queue"] = []
            if not isinstance(disc.get("seen_repos"), list):
                disc["seen_repos"] = []
            merged["discovery"] = disc
        else:
            merged[key] = value
    return merged


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        state = deepcopy(DEFAULT_STATE)
        save_state(state, path)
        return state

    with path.open("r", encoding="utf-8") as file:
        raw = json.load(file)

    if not isinstance(raw, dict):
        state = deepcopy(DEFAULT_STATE)
        save_state(state, path)
        return state

    return _merge_state(raw)


def save_state(state: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=4)
