import logging

import structlog


def configure_logging(debug: bool = False) -> None:
    """
    Configure structlog for structured JSON output.

    Every log line includes timestamp, level, and component.
    Call-related events must include call_uuid, account_id, caller_id
    (bind these via logger.bind() at the top of each request handler).
    """
    log_level = logging.DEBUG if debug else logging.INFO

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(format="%(message)s", level=log_level)
