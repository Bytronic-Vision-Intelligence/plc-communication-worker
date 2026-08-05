#!/usr/bin/env python3
"""Pulse a single Vecow DO pin (hardware only, no MQTT)."""

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
    parser = argparse.ArgumentParser(description="Pulse a single Vecow DO pin")
    parser.add_argument("pin", type=int, nargs="?", default=3, help="DO pin 0..7")
    parser.add_argument("--pulse", type=float, default=0.5, help="seconds high")
    args = parser.parse_args(argv)

    if not 0 <= args.pin <= 7:
        print(f"pin must be 0..7, got {args.pin}", file=sys.stderr)
        return 2

    io = VecowIO()
    if not getattr(io, "initialized_io", False):
        print(f"ERROR: DIO init failed (dll_dir={io.dll_dir})", file=sys.stderr)
        return 1

    print(f"Using DLLs from: {io.dll_dir}")
    mask = 1 << args.pin
    print(f"DO{args.pin} HI  (0b{mask:08b}) for {args.pulse:.3f}s")
    if not io.set_do(mask):
        print("set_do high failed", file=sys.stderr)
        io.close()
        return 1

    time.sleep(args.pulse)
    print(f"DO{args.pin} LO")
    io.set_do(0)
    io.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
