"""Narratron server entry point."""

import logging
import warnings

import uvicorn

# Importing the viewer registers every HTTP and WebSocket route on the shared app.
import web_viewer_app  # noqa: F401
from object_registry import FLAGS, app


class LogFilter(logging.Filter):
    def __init__(self, prefix: str = "", filter_polling: bool = True):
        super().__init__()
        self.prefix = prefix
        self.filter_polling = filter_polling

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if self.filter_polling and ("/api/latest" in message or "/agent/status" in message):
            return False
        return not self.prefix or self.prefix in message or self.prefix in record.name


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    log_filter = LogFilter(FLAGS.log_prefix, FLAGS.suppress_polling)
    for handler in logging.getLogger().handlers:
        handler.addFilter(log_filter)
    logging.getLogger("uvicorn.access").addFilter(log_filter)
    logging.getLogger("PIL").setLevel(logging.INFO)
    warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")


if __name__ == "__main__":
    configure_logging()
    logging.getLogger(__name__).info("Starting server on %s:%s", FLAGS.host, FLAGS.port)
    uvicorn.run(app, host=FLAGS.host, port=FLAGS.port)
