"""Entrypoint: python -m bot"""

from __future__ import annotations

import logging
import sys
from time import sleep

from bot.config import load_settings
from bot.follower import run_cycle


def main() -> None:
    settings = load_settings(interactive=True)
    settings.log_file.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(settings.log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )
    logger = logging.getLogger(__name__)

    try:
        while True:
            settings = load_settings()
            try:
                run_cycle(settings)
            except Exception as exc:
                logger.exception("Cycle failed: %s", exc)
            logger.info("Sleeping %ss before next cycle", settings.loop_sleep_seconds)
            sleep(settings.loop_sleep_seconds)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
