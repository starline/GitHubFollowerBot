"""Persist bot progress in state.json."""

from __future__ import annotations

import json
from pathlib import Path

from bot.config import load_settings

DEFAULT_STATE = {
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


def _state_path() -> Path:
    return load_settings().state_file


def load_state() -> dict:
    path = _state_path()
    if not path.exists():
        save_state(DEFAULT_STATE.copy())
        return DEFAULT_STATE.copy()

    with path.open("r", encoding="utf-8") as file:
        state = json.load(file)

    merged = DEFAULT_STATE.copy()
    merged.update(state)
    return merged


def save_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=4)
