import json
import logging
import sys


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> None:
    """Configure the root logger.

    If ``LOG_FORMAT_JSON`` is true in settings, logs are emitted as JSON
    lines for easy ingestion by log aggregators. Otherwise a simple
    human-readable format is used. This never logs secrets (service keys,
    provider credentials) - callers must not pass them into log messages.
    """
    from config.settings import settings

    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)

    if settings.LOG_FORMAT_JSON:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Suppress overly verbose logs from third-party libraries
    for noisy in ["uvicorn", "asyncio", "sqlalchemy.engine"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)
