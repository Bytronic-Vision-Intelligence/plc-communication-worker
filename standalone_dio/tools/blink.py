#!/usr/bin/env python3
"""Blink one DO pin repeatedly until Ctrl+C."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dio.dio_controller import VecowIO


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Blink a Vecow DO pin")
    parser.add_argument("pin", type=int, nargs="?", default=0)
    parser.add_argument("--on", type=float, default=0.5)
    parser.add_argument("--off", type=float, default=0.5)
    args = parser.parse_args(argv)

    if not 0 <= args.pin <= 7:
        print("pin must be 0..7", file=sys.stderr)
        return 2

    io = VecowIO()
    if not io.initialized_io:
        print(f"ERROR: DIO init failed (dll_dir={io.dll_dir})", file=sys.stderr)
        return 1

    mask = 1 << args.pin
    print(f"Blinking DO{args.pin} from {io.dll_dir} (Ctrl+C to stop)")
    try:
        while True:
            io.set_do(mask)
            time.sleep(args.on)
            io.set_do(0)
            time.sleep(args.off)
    except KeyboardInterrupt:
        pass
    finally:
        io.set_do(0)
        io.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
