"""Standalone DIO worker that listens for pin commands and toggles outputs."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from .dio_controller import VecowIO

try:
    import paho.mqtt.client as mqtt

    _PAHO_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PAHO_AVAILABLE = False

import yaml

logger = logging.getLogger(__name__)

_MAX_FLAT_PIN = 7
_DEFAULT_DO_TOPIC = "dio/output"
_DEFAULT_DI_TOPIC = "dio/input"


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _load_config(path: str | Path) -> dict[str, Any]:
    cfg_path = Path(path)
    if not cfg_path.is_file():
        raise FileNotFoundError(f"Config not found: {cfg_path}")
    with cfg_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a mapping: {cfg_path}")
    return data


def _resolve_worker_settings(cfg: dict[str, Any] | None) -> dict[str, Any]:
    cfg = cfg or {}
    mqtt_cfg = dict(cfg.get("mqtt") or {})
    dio_cfg = dict(cfg.get("dio") or {})
    topics = dict(dio_cfg.get("topics") or {})

    do_topic = (
        topics.get("do")
        or dio_cfg.get("topic")
        or _DEFAULT_DO_TOPIC
    )
    di_topic = topics.get("di") or _DEFAULT_DI_TOPIC

    return {
        "broker": str(mqtt_cfg.get("broker") or dio_cfg.get("broker") or "localhost"),
        "port": int(mqtt_cfg.get("port") or dio_cfg.get("port") or 1883),
        "username": mqtt_cfg.get("username") or "",
        "password": mqtt_cfg.get("password") or "",
        "qos": int(mqtt_cfg.get("qos", 1)),
        "do_topic": str(do_topic),
        "di_topic": str(di_topic),
        "do_count": int(dio_cfg.get("do_count", dio_cfg.get("dio_count", 8))),
        "di_count": int(dio_cfg.get("di_count", 0)),
        "dio_reset_delay": float(dio_cfg.get("dio_reset_delay", 2.0)),
        "di_poll_hz": float(dio_cfg.get("di_poll_hz", 20.0)),
    }


def _set_do_pin(controller: VecowIO, pin_idx: int, value: int) -> None:
    if not 0 <= pin_idx <= _MAX_FLAT_PIN:
        logger.warning("Ignoring out-of-range DO pin %s (valid 0..%s)", pin_idx, _MAX_FLAT_PIN)
        return
    controller.set_do_pin(pin_idx, value)


def _apply_pins(
    controller: VecowIO,
    pins: list[int],
    *,
    do_count: int,
    reset_delay: float,
    output_type: str,
) -> None:
    vec = [int(v) for v in pins[:do_count]]
    if len(vec) < do_count:
        vec.extend([0] * (do_count - len(vec)))

    kind = str(output_type or "pulse").lower()
    if kind == "toggle":
        for idx, value in enumerate(vec):
            _set_do_pin(controller, idx, int(value))
        return

    high = [idx for idx, value in enumerate(vec) if value]
    for idx in high:
        _set_do_pin(controller, idx, 1)

    if reset_delay <= 0 or not high:
        return

    def _reset() -> None:
        time.sleep(reset_delay)
        for idx in high:
            _set_do_pin(controller, idx, 0)

    threading.Thread(target=_reset, name="dio_reset", daemon=True).start()


def _read_di_vector(controller: VecowIO, di_count: int) -> list[int] | None:
    if di_count <= 0:
        return []
    out: list[int] = []
    for idx in range(di_count):
        if idx > _MAX_FLAT_PIN:
            logger.warning("di_count=%s exceeds DIO1 pins; truncating", di_count)
            break
        try:
            val = controller.get_di_pin(idx)
        except Exception:
            return None
        if val is None or isinstance(val, tuple) or val is False:
            return None
        out.append(1 if int(val) else 0)
    return out


def _di_edges(prev: list[int], curr: list[int]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for i, (a, b) in enumerate(zip(prev, curr)):
        if a == b:
            continue
        edges.append(
            {
                "pin": i,
                "edge": "rising" if b else "falling",
                "value": int(b),
            }
        )
    return edges


def _iter_pin_vectors(payload: object) -> Iterable[dict]:
    if isinstance(payload, dict):
        if isinstance(payload.get("commands"), list):
            for cmd in payload["commands"]:
                if isinstance(cmd, dict):
                    yield cmd
            return
        if "pins" in payload:
            yield payload
            return
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run DIO pin worker over MQTT (topics from dio.topics in config)."
    )
    parser.add_argument(
        "--config",
        help="YAML config (mqtt + dio sections)",
    )
    parser.add_argument("--broker", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--topic", default=None, help="Override dio.topics.do")
    parser.add_argument("--do-count", type=int, default=None)
    parser.add_argument("--di-count", type=int, default=None)
    parser.add_argument("--dio-reset-delay", type=float, default=None)
    args = parser.parse_args(argv)

    _configure_logging()
    if not _PAHO_AVAILABLE:
        raise RuntimeError("paho-mqtt is required (pip install paho-mqtt)")

    if not args.config:
        default = Path(__file__).resolve().parent.parent / "config.yaml"
        args.config = str(default) if default.is_file() else None

    file_cfg = _load_config(args.config) if args.config else None
    settings = _resolve_worker_settings(file_cfg)

    if args.broker is not None:
        settings["broker"] = args.broker
    if args.port is not None:
        settings["port"] = args.port
    if args.topic is not None:
        settings["do_topic"] = args.topic
    if args.do_count is not None:
        settings["do_count"] = args.do_count
    if args.di_count is not None:
        settings["di_count"] = args.di_count
    if args.dio_reset_delay is not None:
        settings["dio_reset_delay"] = args.dio_reset_delay

    if settings["do_count"] < 0 or settings["di_count"] < 0:
        raise ValueError("do-count and di-count must be >= 0")

    io = VecowIO()
    if not io.initialized_io:
        logger.error(
            "Vecow DIO failed to initialize (dll_dir=%s). "
            "Check vendor/vecow DLLs + IOConfig, or set VECOW_DLL_DIR.",
            getattr(io, "dll_dir", None),
        )
        return 1
    logger.info("Vecow DIO ready dll_dir=%s", io.dll_dir)
    client = mqtt.Client(client_id=f"standalone_dio_worker_{int(time.time())}")
    username = settings["username"]
    if username:
        client.username_pw_set(username, settings["password"])

    do_topic = settings["do_topic"]
    di_topic = settings["di_topic"]
    qos = settings["qos"]
    di_count = settings["di_count"]
    poll_hz = max(1.0, float(settings.get("di_poll_hz") or 20.0))
    poll_period = 1.0 / poll_hz
    stop = threading.Event()

    def _on_connect(client, _userdata, _flags, rc):
        if rc == 0:
            logger.info(
                "DIO worker connected broker=%s:%s do=%s di=%s "
                "(do_count=%s di_count=%s poll=%.0fHz)",
                settings["broker"],
                settings["port"],
                do_topic,
                di_topic,
                settings["do_count"],
                di_count,
                poll_hz,
            )
            client.subscribe(do_topic, qos=qos)
        else:
            logger.error("MQTT connect failed rc=%s", rc)

    def _on_message(client, _userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            logger.warning("Invalid DIO payload: %r", msg.payload[:100])
            return

        for cmd in _iter_pin_vectors(payload):
            pins = list(cmd.get("pins") or [])
            if not pins and str(cmd.get("type", "pulse")).lower() != "toggle":
                continue
            _apply_pins(
                io,
                pins,
                do_count=settings["do_count"],
                reset_delay=settings["dio_reset_delay"],
                output_type=str(cmd.get("type", "pulse")),
            )

    def _di_poll_loop() -> None:
        if di_count <= 0:
            return
        prev = [0] * di_count
        primed = False
        while not stop.wait(poll_period):
            curr = _read_di_vector(io, di_count)
            if curr is None:
                continue
            if not primed:
                prev = curr
                primed = True
                logger.info("DIO DI initial read pins=%s", curr)
                client.publish(
                    di_topic,
                    json.dumps(
                        {
                            "pins": curr,
                            "edges": [],
                            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                        }
                    ),
                    qos=qos,
                )
                continue
            edges = _di_edges(prev, curr)
            if not edges and curr == prev:
                continue
            prev = curr
            logger.info(
                "DIO DI read pins=%s edges=%s",
                curr,
                [f"{e['pin']}:{e['edge']}={e['value']}" for e in edges],
            )
            client.publish(
                di_topic,
                json.dumps(
                    {
                        "pins": curr,
                        "edges": edges,
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                ),
                qos=qos,
            )

    client.on_connect = _on_connect
    client.on_message = _on_message
    client.connect(settings["broker"], settings["port"], 60)
    di_thread = None
    if di_count > 0:
        di_thread = threading.Thread(
            target=_di_poll_loop, name="dio_di_poll", daemon=True
        )
        di_thread.start()

    def _request_stop(_signum=None, _frame=None) -> None:
        stop.set()
        try:
            client.disconnect()
        except Exception:
            pass

    signal.signal(signal.SIGINT, _request_stop)
    try:
        signal.signal(signal.SIGTERM, _request_stop)
    except (ValueError, OSError):
        pass

    try:
        client.loop_forever()
    finally:
        stop.set()
        if di_thread and di_thread.is_alive():
            di_thread.join(timeout=2.0)
        try:
            client.disconnect()
        finally:
            try:
                if io.initialized_io:
                    io.set_do(0b00000000)
                    logger.info("DIO worker: all DO pins set low")
            except Exception:
                logger.exception("DIO worker failed to clear DO pins")
            io.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
