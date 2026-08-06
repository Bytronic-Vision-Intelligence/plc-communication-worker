#!/usr/bin/env python3
"""app_v2 DIO entrypoint: pin bridge or one-shot DO emit.

Usage:
  python main.py
  python main.py run --broker 192.168.1.10
  python main.py emit 1 0 1 0 0 0 0 0
  python main.py emit 1 0 0 0 0 0 0 0 --bank 1 --delay 0.5 --capture-time now
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Dependencies.bridge import main as run_bridge  # noqa: E402
from Dependencies.config import load_config  # noqa: E402

DEFAULT_CONFIG = ROOT / "config.yaml"
_CAPTURE_FMT = "%Y-%m-%d %H:%M:%S"


def _default_config_arg(argv: list[str]) -> list[str]:
    """Inject --config path when missing and default file exists."""
    if "--config" not in argv and DEFAULT_CONFIG.is_file():
        return ["--config", str(DEFAULT_CONFIG), *argv]
    return argv


def cmd_run(argv: list[str]) -> int:
    return run_bridge(_default_config_arg(argv))


def cmd_emit(argv: list[str] | None = None) -> int:
    try:
        import paho.mqtt.client as mqtt
    except ImportError:  # pragma: no cover
        print("paho-mqtt is required (pip install paho-mqtt)", file=sys.stderr)
        return 1

    parser = argparse.ArgumentParser(
        prog="main.py emit",
        description="Publish a one-shot DO command over MQTT",
    )
    parser.add_argument(
        "pins",
        nargs="+",
        type=int,
        help="0/1 bit vector, e.g. 1 0 1 0 0 0 0 0",
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--bank", type=int, default=None, help="Optional bank (1 or 2)")
    parser.add_argument("--delay", type=float, default=0.0, help="Seconds before activate")
    parser.add_argument(
        "--capture-time",
        default=None,
        help='Date string "YYYY-MM-DD HH:MM:SS", or "now"',
    )
    parser.add_argument(
        "--dio-reset-delay",
        type=float,
        default=None,
        help="Seconds to hold pins high (omit to use config default)",
    )
    parser.add_argument("--broker", default=None, help="Override mqtt.broker")
    parser.add_argument("--port", type=int, default=None, help="Override mqtt.port")
    parser.add_argument("--topic", default=None, help="Override dio.topics.do")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = load_config(args.config)
    mqtt_cfg = getattr(cfg, "mqtt", None)
    dio_cfg = getattr(cfg, "dio", None)

    broker = args.broker or (
        getattr(mqtt_cfg, "broker", "localhost") if mqtt_cfg else "localhost"
    )
    port = args.port if args.port is not None else int(
        getattr(mqtt_cfg, "port", 1883) if mqtt_cfg else 1883
    )
    qos = int(getattr(mqtt_cfg, "qos", 1) if mqtt_cfg else 1)
    username = getattr(mqtt_cfg, "username", "") if mqtt_cfg else ""
    password = getattr(mqtt_cfg, "password", "") if mqtt_cfg else ""

    topics = getattr(dio_cfg, "topics", None) if dio_cfg else None
    topic = args.topic or (
        getattr(topics, "do", "dio/output") if topics else "dio/output"
    )

    pins = [1 if int(p) else 0 for p in args.pins]

    capture_time = None
    if args.capture_time is not None:
        if str(args.capture_time).lower() == "now":
            capture_time = datetime.now().strftime(_CAPTURE_FMT)
        else:
            datetime.strptime(str(args.capture_time), _CAPTURE_FMT)
            capture_time = str(args.capture_time)

    payload: dict = {
        "pins": pins,
        "delay": float(args.delay),
    }
    if args.bank is not None:
        payload["bank"] = int(args.bank)
    if capture_time is not None:
        payload["capture_time"] = capture_time
    if args.dio_reset_delay is not None:
        payload["dio_reset_delay"] = float(args.dio_reset_delay)

    client = mqtt.Client(client_id=f"app_v2_dio_emit_{int(time.time())}")
    if username:
        client.username_pw_set(username, password)
    client.connect(str(broker), int(port), 60)
    client.loop_start()
    try:
        body = json.dumps(payload)
        client.publish(str(topic), body, qos=qos)
        logging.info("Published %s -> %s", body, topic)
        time.sleep(0.3)
    finally:
        client.loop_stop()
        client.disconnect()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0].startswith("-"):
        return cmd_run(argv)

    cmd = argv[0]
    rest = argv[1:]

    if cmd in ("run", "serve", "bridge"):
        return cmd_run(rest)
    if cmd in ("emit", "publish", "do"):
        return cmd_emit(rest)
    if cmd in ("-h", "--help", "help"):
        print(
            "usage: main.py [run] [run options]\n"
            "       main.py emit BIT [BIT ...] [emit options]\n"
            "\n"
            "run (default)   MQTT pin bridge (Vecow hardware)\n"
            "emit            Publish a one-shot DO bit-vector over MQTT\n"
            "\n"
            "Use main.py run -h or main.py emit -h for mode options.",
            file=sys.stderr,
        )
        return 0

    print(
        f"Unknown command {cmd!r}. Use 'run' or 'emit'.\n"
        "  python main.py run\n"
        "  python main.py emit 1 0 1 0 0 0 0 0",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
