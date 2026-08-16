"""Narratron server entry point."""

import logging
import warnings

import uvicorn

# Importing the API package registers every HTTP and WebSocket route on the shared app.
import api_server.app  # noqa: F401
from object_registry import FLAGS, app


class LogFilter(logging.Filter):
    def __init__(self, prefixes: str = "", filter_polling: bool = True):
        super().__init__()
        self.prefixes = tuple(prefix.strip() for prefix in prefixes.split(",") if prefix.strip())
        self.filter_polling = filter_polling

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if self.filter_polling and ("/api/latest" in message or "/agent/status" in message):
            return False
        return not self.prefixes or any(prefix in message or prefix in record.name for prefix in self.prefixes)


def configure_logging() -> None:
    level = logging.DEBUG if FLAGS.log_prefixes else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    # api_server.app may have configured handlers before this entry point is
    # reached, so basicConfig alone cannot reliably change the effective level.
    logging.getLogger().setLevel(level)
    log_filter = LogFilter(FLAGS.log_prefixes, FLAGS.suppress_polling)
    for handler in logging.getLogger().handlers:
        handler.addFilter(log_filter)
    logging.getLogger("uvicorn.access").addFilter(log_filter)
    logging.getLogger("PIL").setLevel(logging.INFO)
    warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")


if __name__ == "__main__":
    configure_logging()
    logging.getLogger(__name__).info("Starting server on %s:%s", FLAGS.host, FLAGS.port)
    uvicorn.run(app, host=FLAGS.host, port=FLAGS.port)
