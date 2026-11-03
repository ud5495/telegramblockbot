from __future__ import annotations

import logging

_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        format=_FORMAT,
        level=getattr(logging, level.upper(), logging.INFO),
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Application").setLevel(logging.INFO)
