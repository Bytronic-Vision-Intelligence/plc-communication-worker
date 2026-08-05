"""DIO service: condition dispatch, pin merge, MQTT publish."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Optional

from .core import RuleResult
from .dataclasses import (
    DioCapabilities,
    DioCommand,
    DioContext,
    DioOutput,
)
from .display import build_vecow_display
from .plugins import load_dio_plugins
from .registry import (
    DioCondition,
    DioOutputType,
    all_handlers,
    handlers_for,
)
from .verdict import inspection_from_result, stamp_result_metadata

logger = logging.getLogger(__name__)

try:
    import paho.mqtt.client as mqtt

    _PAHO_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PAHO_AVAILABLE = False


def _as_dict(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {k: _as_dict(v) if hasattr(v, "__dict__") and not isinstance(v, type) else v
                for k, v in value.items()}
    if hasattr(value, "__dict__") and not isinstance(value, type):
        out: dict[str, Any] = {}
        for k, v in vars(value).items():
            if hasattr(v, "__dict__") and not isinstance(v, type) and not isinstance(v, dict):
                out[k] = _as_dict(v)
            elif isinstance(v, dict):
                out[k] = _as_dict(v)
            else:
                out[k] = v
        return out
    return {}


class DioService:
    """Dispatch DIO handlers by condition and publish pin commands over MQTT."""

    def __init__(self, cfg):
        self._cfg = cfg
        self._dio_cfg = _as_dict(getattr(cfg, "dio", None))
        self._mqtt_cfg = _as_dict(getattr(cfg, "mqtt", None))
        # Nested topics may still be SimpleNamespace
        if "topics" in self._dio_cfg and not isinstance(self._dio_cfg["topics"], dict):
            self._dio_cfg["topics"] = _as_dict(self._dio_cfg["topics"])
        if "topics" in self._mqtt_cfg and not isinstance(self._mqtt_cfg["topics"], dict):
            self._mqtt_cfg["topics"] = _as_dict(self._mqtt_cfg["topics"])

        self._enabled = bool(getattr(cfg, "dio_enabled", True))
        self._client: Optional[object] = None
        self._publish_enabled = False
        self._di_count = int(self._dio_cfg.get("di_count", 0))
        self._do_count = int(
            self._dio_cfg.get("do_count", self._dio_cfg.get("dio_count", 8))
        )
        dio_topics = dict(self._dio_cfg.get("topics") or {})
        mqtt_topics = dict(self._mqtt_cfg.get("topics") or {})
        self._topic = str(
            dio_topics.get("do")
            or self._dio_cfg.get("topic")
            or mqtt_topics.get("dio")
            or "dio/output"
        )
        self._di_topic = str(dio_topics.get("di") or "dio/input")

        self._capabilities = DioCapabilities()
        self._do_state = [0] * self._do_count
        self._toggle_layers: dict[str, list[int]] = {}
        self._di_state = [0] * self._di_count
        self._di_toggle_layers: dict[str, list[int]] = {}
        self._di_visual_layers: dict[str, list[int]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._interval_thread: Optional[threading.Thread] = None
        self._display_sink: Callable[..., None] | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def bind_capabilities(
        self,
        *,
        web_up: Callable[[], bool] | None = None,
        processes_alive: Callable[[], bool] | None = None,
    ) -> None:
        if web_up is not None:
            self._capabilities.web_up = web_up
        if processes_alive is not None:
            self._capabilities.processes_alive = processes_alive

    def bind_display_sink(self, sink: Callable[..., None] | None) -> None:
        """Optional callback(display, held_pins, commands) for HMI updates."""
        self._display_sink = sink

    def start(self) -> None:
        if not self._enabled:
            return

        load_dio_plugins(self._cfg)
        try:
            self._connect_mqtt()
            self._publish_enabled = True
            topics = self._mqtt_cfg.get("topics") or {}
            command_topic = str(topics.get("command", "vision/command"))
            self._capabilities.publish_command = lambda payload, _t=command_topic: (
                self._publish(_t, json.dumps(payload))
            )
        except Exception as exc:
            self._publish_enabled = False
            logger.warning(
                "DIO transport unavailable; running mapping-only mode: %s", exc
            )

        n = len(all_handlers())
        logger.info(
            "DIO service enabled handlers=%s di=%s do=%s (publish=%s)",
            n,
            self._di_count,
            self._do_count,
            self._publish_enabled,
        )

        self.emit(DioCondition.STARTUP)
        self.emit(DioCondition.CONFIG)
        self._start_interval_loop()

    def shutdown(self) -> None:
        self._stop.set()
        if self._interval_thread and self._interval_thread.is_alive():
            self._interval_thread.join(timeout=2.0)
        self._interval_thread = None
        try:
            self.emit(DioCondition.SHUTDOWN)
        except Exception:
            logger.exception("DIO SHUTDOWN handlers failed")
        try:
            self._clear_all_do()
        except Exception:
            logger.exception("DIO shutdown failed to clear DO outputs")
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
            self._client = None

    def _clear_all_do(self) -> None:
        with self._lock:
            self._toggle_layers.clear()
            self._do_state = [0] * self._do_count
            pins = list(self._do_state)
        if not self._publish_enabled or not self._client:
            return
        payload = {
            "condition": "shutdown",
            "commands": [
                DioCommand(pins=pins, type=DioOutputType.TOGGLE).to_dict()
            ],
        }
        self._publish(self._topic, json.dumps(payload))
        time.sleep(0.25)
        logger.info("DIO shutdown: published all-low DO (%s pins)", len(pins))

    def on_verdict(self, rr: RuleResult) -> None:
        self.emit(DioCondition.VERDICT, rule_result=rr)

    def on_result_ready(self, rr: RuleResult) -> None:
        self.emit(
            DioCondition.RESULT_READY,
            rule_result=rr,
            extra={"result_ready": True},
        )

    def on_camera_trigger(self, camera_id: str | None = None) -> None:
        self.emit(DioCondition.CAMERA_TRIGGER, camera_id=camera_id)

    def on_di_message(self, payload: dict[str, Any]) -> None:
        pins = [int(v) for v in list(payload.get("pins") or [])[: self._di_count or None]]
        if self._di_count and len(pins) < self._di_count:
            pins.extend([0] * (self._di_count - len(pins)))

        edges = [e for e in (payload.get("edges") or []) if isinstance(e, dict)]
        if not edges:
            logger.info("DIO DI read snapshot pins=%s", pins)
            self.emit(
                DioCondition.DI,
                extra={"di_pins": pins, "snapshot": True},
            )
            return

        edge_parts = []
        for edge in edges:
            pin = int(edge["pin"])
            edge_kind = str(edge.get("edge", "")).lower()
            value = int(edge.get("value", 1 if edge_kind == "rising" else 0))
            edge_parts.append(f"{pin}:{edge_kind}={value}")
            self.emit(
                DioCondition.DI,
                extra={
                    "di_pins": pins,
                    "pin": pin,
                    "edge": edge_kind,
                    "value": value,
                    "snapshot": False,
                },
            )
        logger.info(
            "DIO DI read pins=%s edges=[%s]",
            pins,
            ", ".join(edge_parts),
        )

    def emit(
        self,
        condition: DioCondition | str,
        *,
        rule_result: RuleResult | None = None,
        camera_id: str | None = None,
        extra: dict | None = None,
    ) -> None:
        if not self._enabled:
            return

        if not isinstance(condition, DioCondition):
            condition = DioCondition(str(condition))

        specs = handlers_for(condition)
        if not specs and condition != DioCondition.DI:
            return

        inspection = None
        if condition == DioCondition.VERDICT:
            inspection = inspection_from_result(rule_result)

        ctx = DioContext(
            condition=condition,
            rule_result=rule_result,
            inspection=inspection,
            camera_id=camera_id or (
                getattr(rule_result, "camera_id", None) if rule_result else None
            ),
            capabilities=self._capabilities,
            extra=dict(extra or {}),
        )

        results: list[tuple[Any, DioOutput | None]] = []
        for spec in specs:
            try:
                output = spec.fn(ctx, self._dio_cfg)
            except Exception:
                logger.exception(
                    "DIO handler %s failed for %s",
                    getattr(spec.fn, "__name__", spec.fn),
                    condition.value,
                )
                results.append((spec, None))
                continue
            results.append((spec, output if isinstance(output, DioOutput) else None))

        pulse_commands: list[DioCommand] = []
        toggle_changed = False
        di_toggle_changed = False
        di_pulse_pins: list[list[int]] = []
        display: dict[str, Any] | None = None

        with self._lock:
            if condition == DioCondition.DI and isinstance((ctx.extra or {}).get("di_pins"), list):
                self._di_state = self._normalize_di_pins(list((ctx.extra or {})["di_pins"]))
            for spec, output in results:
                if output is None:
                    continue
                di_pins_from_display = None
                di_section = None
                if isinstance(output.display, dict) and isinstance(output.display.get("di"), dict):
                    di_section = output.display["di"]
                    if isinstance(di_section.get("pins"), list):
                        di_pins_from_display = self._normalize_di_pins(di_section["pins"])
                elif (
                    isinstance(output.display, dict)
                    and condition == DioCondition.DI
                    and isinstance((ctx.extra or {}).get("di_pins"), list)
                ):
                    di_pins_from_display = self._normalize_di_pins(list((ctx.extra or {})["di_pins"]))

                if di_pins_from_display is not None:
                    visual_only = bool(
                        isinstance(di_section, dict) and di_section.get("source") == "sim"
                    )
                    layer_key = f"{id(spec.fn)}:{getattr(spec.fn, '__name__', 'fn')}"
                    if visual_only:
                        if spec.output_type == DioOutputType.TOGGLE:
                            self._di_visual_layers[layer_key] = list(di_pins_from_display)
                        elif any(di_pins_from_display):
                            di_pulse_pins.append(di_pins_from_display)
                    elif spec.output_type == DioOutputType.TOGGLE:
                        self._di_toggle_layers[layer_key] = list(di_pins_from_display)
                        di_toggle_changed = True
                    elif any(di_pins_from_display):
                        di_pulse_pins.append(di_pins_from_display)

                if output.is_empty:
                    if output.display is not None:
                        display = output.display
                    continue

                for cmd in output.commands:
                    pins = self._normalize_pins(cmd.pins)
                    cmd.pins = pins
                    cmd.type = spec.output_type

                    if spec.output_type == DioOutputType.TOGGLE:
                        layer_key = f"{id(spec.fn)}:{getattr(spec.fn, '__name__', 'fn')}"
                        self._toggle_layers[layer_key] = list(pins)
                        toggle_changed = True
                    elif any(pins):
                        pulse_commands.append(cmd)

                if output.display is not None:
                    display = output.display

            if toggle_changed:
                self._recompute_do_state()
            if di_toggle_changed:
                self._recompute_di_state()

            publish_cmds: list[DioCommand] = []
            if toggle_changed:
                publish_cmds.append(
                    DioCommand(pins=list(self._do_state), type=DioOutputType.TOGGLE)
                )
            publish_cmds.extend(pulse_commands)

            display = self._build_display(ctx, display, pulse_commands, di_pulse_pins)

            held = list(self._do_state)
            has_output = bool(publish_cmds) or toggle_changed

            if rule_result is not None and has_output:
                stamp_result_metadata(
                    rule_result,
                    publish_cmds,
                    display,
                    held_pins=held,
                    di_count=self._di_count,
                    do_count=self._do_count,
                )

            sink_args = None
            skip_hw_di_display = (
                condition == DioCondition.DI and bool(self._di_visual_layers)
            )
            if (
                self._display_sink is not None
                and not skip_hw_di_display
                and (has_output or condition == DioCondition.DI or display is not None)
            ):
                sink_args = (
                    display,
                    held,
                    [cmd.to_dict() for cmd in publish_cmds],
                )

            payloads: list[dict[str, Any]] = []
            if publish_cmds:
                base_payload = {
                    "camera_id": camera_id
                    or (getattr(rule_result, "camera_id", "") if rule_result else ""),
                    "inspection_id": (
                        (rule_result.metadata or {}).get("inspection_id", "")
                        if rule_result
                        else ""
                    ),
                    "frame_index": getattr(rule_result, "frame_index", None)
                    if rule_result
                    else None,
                    "condition": condition.value,
                    "display": display,
                }
                payloads = [
                    {**base_payload, "commands": [cmd.to_dict()]}
                    for cmd in publish_cmds
                ]

        if sink_args is not None:
            try:
                self._display_sink(*sink_args)
            except Exception:
                logger.exception("DIO display sink failed")

        if payloads and self._publish_enabled:
            for payload in payloads:
                self._publish(self._topic, json.dumps(payload))

    def _recompute_do_state(self) -> None:
        state = [0] * self._do_count
        for layer in self._toggle_layers.values():
            for i, v in enumerate(layer[: self._do_count]):
                if v:
                    state[i] = 1
        self._do_state = state

    def _normalize_pins(self, pins: list[int]) -> list[int]:
        vec = [int(v) for v in pins]
        if len(vec) < self._do_count:
            vec.extend([0] * (self._do_count - len(vec)))
        return vec[: self._do_count]

    def _normalize_di_pins(self, pins: list[int]) -> list[int]:
        vec = [int(v) for v in pins]
        if len(vec) < self._di_count:
            vec.extend([0] * (self._di_count - len(vec)))
        return vec[: self._di_count]

    def _recompute_di_state(self) -> None:
        state = [0] * self._di_count
        for layer in self._di_toggle_layers.values():
            for i, v in enumerate(layer[: self._di_count]):
                if v:
                    state[i] = 1
        self._di_state = state

    def _di_visual_pins(self) -> list[int]:
        state = [0] * self._di_count
        for layer in self._di_visual_layers.values():
            for i, v in enumerate(layer[: self._di_count]):
                if v:
                    state[i] = 1
        return state

    def _build_display(
        self,
        ctx: DioContext,
        plugin_display: dict[str, Any] | None,
        pulse_commands: list[DioCommand],
        di_pulse_pins: list[list[int]],
    ) -> dict[str, Any]:
        plugin = dict(plugin_display or {})
        do_pins = list(self._do_state)
        if pulse_commands:
            for cmd in pulse_commands:
                for i, v in enumerate(cmd.pins):
                    if v and i < len(do_pins):
                        do_pins[i] = 1
        elif isinstance(plugin.get("pins"), list):
            do_pins = list(plugin["pins"])

        do_labels = plugin.get("labels")
        if isinstance(plugin.get("do"), dict):
            do_section = plugin["do"]
            if isinstance(do_section.get("pins"), list):
                do_pins = list(do_section["pins"])
            if do_section.get("labels") is not None:
                do_labels = do_section["labels"]

        di_pins = (
            self._di_visual_pins() if self._di_visual_layers else list(self._di_state)
        )
        di_labels = None
        if isinstance(plugin.get("di"), dict):
            di_section = plugin["di"]
            if isinstance(di_section.get("pins"), list):
                section_pins = self._normalize_di_pins(list(di_section["pins"]))
                for i, v in enumerate(section_pins):
                    if v and i < len(di_pins):
                        di_pins[i] = 1
            if di_section.get("labels") is not None:
                di_labels = di_section["labels"]
        elif not self._di_visual_layers and isinstance(
            (ctx.extra or {}).get("di_pins"), list
        ):
            di_pins = self._normalize_di_pins(list(ctx.extra["di_pins"]))

        if di_pulse_pins:
            for pulse in di_pulse_pins:
                for i, v in enumerate(pulse):
                    if v and i < len(di_pins):
                        di_pins[i] = 1

        return build_vecow_display(
            di_count=self._di_count,
            do_count=self._do_count,
            do_pins=do_pins,
            di_pins=di_pins,
            do_labels=do_labels if isinstance(do_labels, dict) else None,
            di_labels=di_labels if isinstance(di_labels, dict) else None,
        )

    def _start_interval_loop(self) -> None:
        specs = handlers_for(DioCondition.INTERVAL)
        if not specs:
            return

        def _loop() -> None:
            due: dict[int, float] = {}
            now = time.monotonic()
            for i, spec in enumerate(specs):
                due[i] = now
            while not self._stop.wait(0.2):
                now = time.monotonic()
                fire = False
                for i, spec in enumerate(specs):
                    interval = float(spec.interval_s or 2.0)
                    if now >= due[i]:
                        fire = True
                        due[i] = now + max(0.1, interval)
                if fire:
                    try:
                        self.emit(DioCondition.INTERVAL)
                    except Exception:
                        logger.exception("DIO interval emit failed")

        self._interval_thread = threading.Thread(
            target=_loop, name="dio_interval", daemon=True
        )
        self._interval_thread.start()

    def _connect_mqtt(self) -> None:
        if not _PAHO_AVAILABLE:
            raise RuntimeError("paho-mqtt is required for DIO service")

        client = mqtt.Client(client_id="standalone_dio_service")
        username = self._mqtt_cfg.get("username")
        if username:
            client.username_pw_set(username, self._mqtt_cfg.get("password", ""))

        def _on_connect(client, _userdata, _flags, rc):
            if rc != 0:
                logger.error("DIO service MQTT connect failed rc=%s", rc)
                return
            qos = int(self._mqtt_cfg.get("qos", 1))
            client.subscribe(self._di_topic, qos=qos)
            logger.info("DIO service subscribed to DI topic %s", self._di_topic)

        def _on_message(_client, _userdata, msg):
            topic = getattr(msg, "topic", "") or ""
            if topic != self._di_topic:
                return
            try:
                payload = json.loads(msg.payload.decode("utf-8"))
            except Exception:
                logger.warning("Invalid DI payload: %r", msg.payload[:100])
                return
            if not isinstance(payload, dict):
                return
            try:
                self.on_di_message(payload)
            except Exception:
                logger.exception("DIO DI message handling failed")

        client.on_connect = _on_connect
        client.on_message = _on_message
        client.connect(
            self._mqtt_cfg.get("broker", "localhost"),
            int(self._mqtt_cfg.get("port", 1883)),
            int(self._mqtt_cfg.get("keepalive", 60)),
        )
        client.loop_start()
        self._client = client

    def _publish(self, topic: str, payload: str) -> None:
        if not self._client:
            return
        qos = int(self._mqtt_cfg.get("qos", 1))
        self._client.publish(topic, payload, qos=qos)
