"""PianoFlow backend entry point.

Run with the project's Python environment (see ``README.md`` for how the
interpreter is located; ``PIANOFLOW_PYTHON`` overrides)::

    python backend_python/app.py --port 8890
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from pathlib import Path

# Allow ``python backend_python/app.py`` without installing the package.
_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import tornado.ioloop  # noqa: E402
import tornado.web  # noqa: E402

from api.routes import make_app  # noqa: E402
from container import build_services  # noqa: E402
from core.config import Settings  # noqa: E402
from core.logging_setup import configure_logging  # noqa: E402

logger = logging.getLogger("pianoflow")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    defaults = Settings.from_env()
    parser = argparse.ArgumentParser(description="PianoFlow AI music workbench backend")
    parser.add_argument("--host", default=defaults.host)
    parser.add_argument("--port", type=int, default=defaults.port)
    return parser.parse_args(argv)


def resolve_settings(argv: list[str] | None = None) -> Settings:
    """Environment settings with CLI overrides applied on top."""
    args = parse_args(argv)
    return dataclasses.replace(Settings.from_env(), host=args.host, port=args.port)


def main(argv: list[str] | None = None) -> int:
    settings = resolve_settings(argv)

    configure_logging(settings.log_dir, settings.log_level)
    services = build_services(settings)
    # No explicit loop binding: TaskManager binds lazily from the first call
    # that runs on the event loop, which avoids the deprecated
    # asyncio.get_event_loop() dance before IOLoop.start().

    app = make_app(settings, services)
    app.listen(settings.port, address=settings.host)
    logger.info("PianoFlow backend listening on http://%s:%s", settings.host, settings.port)
    logger.info("cache dir: %s", settings.cache_dir)
    logger.info("music sources: %s", ", ".join(services.extractors.names()))

    loop = tornado.ioloop.IOLoop.current()

    async def shutdown() -> None:
        # The shared HTTP client is long lived; close it explicitly so sockets
        # are released instead of being reaped at interpreter exit.
        await services.http.aclose()
        logger.info("PianoFlow backend stopped")

    try:
        loop.start()
    except KeyboardInterrupt:
        logger.info("interrupted, shutting down")
    finally:
        try:
            loop.run_sync(shutdown)
        except Exception:
            logger.exception("shutdown failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
