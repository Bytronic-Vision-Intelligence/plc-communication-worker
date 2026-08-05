#!/usr/bin/env python3
"""Emit a sample PASS/FAIL verdict through DioService (for MQTT testing).

Requires the mapping service logic only (and optionally a running worker + broker).

Usage:
  python emit_verdict.py pass
  python emit_verdict.py fail
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dio import DioService, RuleResult, Verdict  # noqa: E402
from dio.config import load_config  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish a one-shot DIO verdict")
    parser.add_argument("verdict", choices=("pass", "fail", "unknown"))
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = load_config(args.config)
    service = DioService(cfg)
    service.start()

    verdict = {
        "pass": Verdict.PASS,
        "fail": Verdict.FAIL,
        "unknown": Verdict.UNKNOWN,
    }[args.verdict]

    rr = RuleResult(
        frame_index=1,
        verdict=verdict,
        camera_id="cam1",
        metadata={
            "inspection_id": "standalone-test",
            "inspection_verdict": verdict.value,
            "inspection_cameras": {"cam1": verdict.value},
        },
    )
    service.on_verdict(rr)
    time.sleep(0.5)
    service.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
