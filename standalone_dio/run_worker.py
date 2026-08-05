#!/usr/bin/env python3
"""Start the Vecow MQTT pin worker using standalone_dio/config.yaml."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dio.worker import main  # noqa: E402

if __name__ == "__main__":
    default = ROOT / "config.yaml"
    argv = list(sys.argv[1:])
    if "--config" not in argv and default.is_file():
        argv = ["--config", str(default), *argv]
    raise SystemExit(main(argv))
