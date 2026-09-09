"""Narratron server entry point."""

import logging
import warnings

import uvicorn

# Importing the API package registers every HTTP and WebSocket route on the shared app.
import api_server.app  # noqa: F401
from api_server.app import LogFilter, suppress_noisy_loggers
from object_registry import FLAGS, app



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
    suppress_noisy_loggers(FLAGS.log_prefixes)
    warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")


if __name__ == "__main__":
    configure_logging()
    logging.getLogger(__name__).info("Starting server on %s:%s", FLAGS.host, FLAGS.port)
    uvicorn.run(app, host=FLAGS.host, port=FLAGS.port)
