"""app_v2 DIO bridge: MQTT pin commands in, pin edge events out.

DO (subscribe ``dio.topics.do``)::

    {
      "bank": 1,                          # optional, default from config
      "pins": [1, 0, 1, 0, 0, 0, 0, 0], # required — 0/1 bit vector
      "delay": 0.0,                       # optional seconds before activate (default 0)
      "capture_time": "2026-04-06 10:00:00",  # optional "YYYY-MM-DD HH:MM:SS"
      "dio_reset_delay": 2.0              # optional hold-high seconds (default: config)
    }

Also accepts a JSON list of such objects (one command per item).

Without ``bank``, a longer vector is split across banks using
``dio.block_size`` (same idea as the legacy app process).

DI (publish ``dio.topics.di`` when pins rise)::

    {"bank": 1, "pins": [0, 0, 1, 0, 0, 1, 0, 0]}  # 1 = pin rose
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
import time
from datetime import datetime
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

_MAX_PIN = 7
_PINS_PER_BANK = 8
_DEFAULT_DO_TOPIC = "dio/output"
_DEFAULT_DI_TOPIC = "dio/input"
_DEFAULT_BANK = 1
_CAPTURE_FMT = "%Y-%m-%d %H:%M:%S"


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


def _resolve_settings(cfg: dict[str, Any] | None) -> dict[str, Any]:
    cfg = cfg or {}
    mqtt_cfg = dict(cfg.get("mqtt") or {})
    dio_cfg = dict(cfg.get("dio") or {})
    topics = dict(dio_cfg.get("topics") or {})

    do_topic = topics.get("do") or dio_cfg.get("topic") or _DEFAULT_DO_TOPIC
    di_topic = topics.get("di") or _DEFAULT_DI_TOPIC
    banks = dio_cfg.get("banks") or [1]
    if isinstance(banks, int):
        banks = [banks]
    banks = [int(b) for b in banks]
    if not banks:
        banks = [_DEFAULT_BANK]

    block_size = int(
        dio_cfg.get("block_size", dio_cfg.get("dio_block_size", _PINS_PER_BANK))
    )
    if block_size <= 0:
        block_size = _PINS_PER_BANK

    return {
        "broker": str(mqtt_cfg.get("broker") or dio_cfg.get("broker") or "localhost"),
        "port": int(mqtt_cfg.get("port") or dio_cfg.get("port") or 1883),
        "username": mqtt_cfg.get("username") or "",
        "password": mqtt_cfg.get("password") or "",
        "qos": int(mqtt_cfg.get("qos", 1)),
        "do_topic": str(do_topic),
        "di_topic": str(di_topic),
        "default_bank": int(dio_cfg.get("default_bank", banks[0])),
        "banks": banks,
        "block_size": block_size,
        "do_count": int(dio_cfg.get("do_count", dio_cfg.get("dio_count", 8))),
        "di_count": int(dio_cfg.get("di_count", 8)),
        "dio_reset_delay": float(dio_cfg.get("dio_reset_delay", 2.0)),
        "di_poll_hz": float(dio_cfg.get("di_poll_hz", 20.0)),
    }


def _parse_bit_vector(raw: Any, *, max_len: int | None = None) -> list[int]:
    """Accept a 0/1 pin state vector, e.g. [1, 0, 1, 0, ...]."""
    if not isinstance(raw, list):
        raise ValueError("pins must be a list of 0/1 values")
    if not raw:
        raise ValueError("pins must not be empty")
    vec = [1 if int(v) else 0 for v in raw]
    if max_len is not None and max_len > 0:
        if len(vec) > max_len:
            logger.warning("Truncating pins from %s to %s", len(vec), max_len)
            vec = vec[:max_len]
    return vec


def _high_indices(vector: list[int]) -> list[int]:
    return [i for i, v in enumerate(vector) if v and i <= _MAX_PIN]


def _effective_wait(delay: float, capture_time: str | None) -> float:
    """Wait max(0, delay - age) when capture_time is 'YYYY-MM-DD HH:MM:SS'."""
    wait = max(0.0, float(delay or 0.0))
    if not capture_time:
        return wait
    try:
        start = datetime.strptime(str(capture_time), _CAPTURE_FMT)
    except (TypeError, ValueError):
        logger.warning("Invalid capture_time %r (want %s)", capture_time, _CAPTURE_FMT)
        return wait
    age = (datetime.now() - start).total_seconds()
    return max(0.0, wait - age)


def _iter_commands(payload: object) -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict):
        if isinstance(payload.get("commands"), list):
            for cmd in payload["commands"]:
                if isinstance(cmd, dict):
                    yield cmd
            return
        yield payload
        return
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item


def _apply_do_bank(
    controller: VecowIO,
    *,
    bank: int,
    vector: list[int],
    reset_delay: float,
) -> None:
    high = _high_indices(vector)
    # Apply full vector state (0 and 1) for pins present in the message.
    for idx, value in enumerate(vector[: _PINS_PER_BANK]):
        controller.set_do_pin(idx, int(value), bank=bank)

    logger.info("DO bank=%s vector=%s high=%s", bank, vector, high)

    if reset_delay <= 0 or not high:
        return

    def _reset() -> None:
        time.sleep(reset_delay)
        controller.set_do_pins(high, 0, bank=bank)
        logger.info(
            "DO bank=%s high pins LOW (reset after %.3fs): %s",
            bank,
            reset_delay,
            high,
        )

    threading.Thread(target=_reset, name=f"dio_reset_b{bank}", daemon=True).start()


def _expand_bank_commands(
    vector: list[int],
    *,
    bank: int | None,
    default_bank: int,
    banks: list[int],
    block_size: int,
) -> list[tuple[int, list[int]]]:
    """Return (bank, per-bank bit vector) slices."""
    if bank is not None:
        return [(bank, vector[:block_size])]

    # Multi-bank flat vector (app-style): [bank1…][bank2…]
    if len(vector) > block_size:
        out: list[tuple[int, list[int]]] = []
        for i, b in enumerate(banks):
            start = i * block_size
            if start >= len(vector):
                break
            out.append((b, vector[start : start + block_size]))
        if out:
            return out
        # Vector longer than one block but banks list shorter — map by index.
        for i in range(0, len(vector), block_size):
            b = i // block_size + 1  # banks numbered from 1
            out.append((b, vector[i : i + block_size]))
        return out

    return [(default_bank, vector)]


def _apply_do(
    controller: VecowIO,
    *,
    bank: int | None,
    vector: list[int],
    delay: float,
    capture_time: str | None,
    reset_delay: float,
    default_bank: int,
    banks: list[int],
    block_size: int,
) -> None:
    if not any(vector):
        logger.warning("DO command with all-low pins; ignoring")
        return

    wait = _effective_wait(delay, capture_time)
    if wait > 0:
        logger.info(
            "DO pins=%s waiting %.3fs (delay=%s capture_time=%s)",
            vector,
            wait,
            delay,
            capture_time,
        )
        time.sleep(wait)

    for b, slice_vec in _expand_bank_commands(
        vector,
        bank=bank,
        default_bank=default_bank,
        banks=banks,
        block_size=block_size,
    ):
        if not any(slice_vec):
            continue
        _apply_do_bank(
            controller, bank=b, vector=slice_vec, reset_delay=reset_delay
        )


def _rising_mask(prev: list[int], curr: list[int]) -> list[int]:
    return [1 if (not a and b) else 0 for a, b in zip(prev, curr)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run DIO pin bridge over MQTT (bit-vector pins)."
    )
    parser.add_argument("--config", help="YAML config (mqtt + dio sections)")
    parser.add_argument("--broker", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--topic", default=None, help="Override dio.topics.do")
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
    settings = _resolve_settings(file_cfg)

    if args.broker is not None:
        settings["broker"] = args.broker
    if args.port is not None:
        settings["port"] = args.port
    if args.topic is not None:
        settings["do_topic"] = args.topic
    if args.di_count is not None:
        settings["di_count"] = args.di_count
    if args.dio_reset_delay is not None:
        settings["dio_reset_delay"] = args.dio_reset_delay

    di_count = max(0, min(8, int(settings["di_count"])))
    settings["di_count"] = di_count
    block_size = max(1, min(8, int(settings["block_size"])))
    settings["block_size"] = block_size

    io = VecowIO()
    if not io.initialized_io:
        logger.error(
            "Vecow DIO failed to initialize (dll_dir=%s). "
            "Check Dependencies/Vecow DLLs + IOConfig, or set VECOW_DLL_DIR.",
            getattr(io, "dll_dir", None),
        )
        return 1
    logger.info("Vecow DIO ready dll_dir=%s", io.dll_dir)

    client = mqtt.Client(client_id=f"app_v2_dio_bridge_{int(time.time())}")
    username = settings["username"]
    if username:
        client.username_pw_set(username, settings["password"])

    do_topic = settings["do_topic"]
    di_topic = settings["di_topic"]
    qos = settings["qos"]
    default_bank = settings["default_bank"]
    banks: list[int] = list(settings["banks"])
    poll_hz = max(1.0, float(settings.get("di_poll_hz") or 20.0))
    poll_period = 1.0 / poll_hz
    stop = threading.Event()

    def _on_connect(client, _userdata, _flags, rc):
        if rc == 0:
            logger.info(
                "DIO bridge connected broker=%s:%s do=%s di=%s "
                "(banks=%s block=%s di_count=%s poll=%.0fHz reset=%.2fs)",
                settings["broker"],
                settings["port"],
                do_topic,
                di_topic,
                banks,
                block_size,
                di_count,
                poll_hz,
                settings["dio_reset_delay"],
            )
            client.subscribe(do_topic, qos=qos)
        else:
            logger.error("MQTT connect failed rc=%s", rc)

    def _on_message(_client, _userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            logger.warning("Invalid DIO payload: %r", msg.payload[:100])
            return

        for cmd in _iter_commands(payload):
            if "pins" not in cmd:
                logger.warning("DO command missing pins: %s", cmd)
                continue
            try:
                vector = _parse_bit_vector(cmd.get("pins"))
            except (TypeError, ValueError) as exc:
                logger.warning("Bad pins in DO command %s: %s", cmd, exc)
                continue

            bank: int | None
            if "bank" in cmd and cmd.get("bank") is not None:
                try:
                    bank = int(cmd["bank"])
                except (TypeError, ValueError):
                    logger.warning(
                        "Invalid bank %r; using default logic", cmd.get("bank")
                    )
                    bank = None
            else:
                bank = None

            delay = cmd.get("delay", 0)
            try:
                delay = float(delay if delay is not None else 0)
            except (TypeError, ValueError):
                logger.warning("Invalid delay %r; using 0", cmd.get("delay"))
                delay = 0.0

            capture_time = cmd.get("capture_time", None)
            if capture_time is not None:
                capture_time = str(capture_time)
                try:
                    datetime.strptime(capture_time, _CAPTURE_FMT)
                except ValueError:
                    logger.warning(
                        "Invalid capture_time %r (want %s); ignoring",
                        capture_time,
                        _CAPTURE_FMT,
                    )
                    capture_time = None

            reset_delay = settings["dio_reset_delay"]
            if "dio_reset_delay" in cmd and cmd.get("dio_reset_delay") is not None:
                try:
                    reset_delay = float(cmd["dio_reset_delay"])
                except (TypeError, ValueError):
                    logger.warning(
                        "Invalid dio_reset_delay %r; using config %.3fs",
                        cmd.get("dio_reset_delay"),
                        settings["dio_reset_delay"],
                    )
                    reset_delay = settings["dio_reset_delay"]

            threading.Thread(
                target=_apply_do,
                kwargs={
                    "controller": io,
                    "bank": bank,
                    "vector": vector,
                    "delay": delay,
                    "capture_time": capture_time,
                    "reset_delay": reset_delay,
                    "default_bank": default_bank,
                    "banks": banks,
                    "block_size": block_size,
                },
                name="dio_do",
                daemon=True,
            ).start()

    def _di_poll_loop() -> None:
        if di_count <= 0:
            return
        prev: dict[int, list[int]] = {b: [0] * di_count for b in banks}
        primed: dict[int, bool] = {b: False for b in banks}
        while not stop.wait(poll_period):
            for bank in banks:
                curr = io.get_di_vector(bank=bank, count=di_count)
                if curr is None:
                    continue
                if not primed[bank]:
                    prev[bank] = curr
                    primed[bank] = True
                    logger.info("DI bank=%s initial pins=%s", bank, curr)
                    continue
                risen = _rising_mask(prev[bank], curr)
                prev[bank] = curr
                if not any(risen):
                    continue
                payload = {"bank": bank, "pins": risen}
                logger.info("DI risen bank=%s pins=%s", bank, risen)
                client.publish(di_topic, json.dumps(payload), qos=qos)

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
                    for bank in banks:
                        io.set_do(0b00000000, bank=bank)
                    logger.info("DIO bridge: all DO pins set low")
            except Exception:
                logger.exception("DIO bridge failed to clear DO pins")
            io.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
