"""Persist bot progress in state.json."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_STATE = {
    "last_followed_user": None,
    "how_many_bot_followed_so_far_counter": 0,
    "last_fetched_user": 0,
}


def _state_path() -> Path:
    return Path(os.getenv("STATE_FILE", "state.json").strip() or "state.json")


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
    with path.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=4)
