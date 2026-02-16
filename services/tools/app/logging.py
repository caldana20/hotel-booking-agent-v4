from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(log_level: str) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level.upper())
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(log_level.upper())),
        cache_logger_on_first_use=True,
    )


logger = structlog.get_logger()

