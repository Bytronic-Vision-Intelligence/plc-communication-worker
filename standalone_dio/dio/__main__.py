"""python -m dio  -> start mapping service from config.yaml."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from .config import DEFAULT_CONFIG, load_config
from .service import DioService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standalone DIO mapping service")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="Path to config.yaml (default: standalone_dio/config.yaml)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = load_config(args.config)
    service = DioService(cfg)
    if not service.enabled:
        logging.error("dio_enabled is false in config — nothing to run")
        return 1

    stop = threading.Event()

    def _request_stop(_signum=None, _frame=None) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _request_stop)
    try:
        signal.signal(signal.SIGTERM, _request_stop)
    except (ValueError, OSError):
        pass

    service.start()
    logging.info("DIO service running (config=%s). Ctrl+C to stop.", args.config)
    try:
        while not stop.wait(0.5):
            pass
    finally:
        service.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
