# Standalone DIO

Self-contained digital I/O package adapted from `packages/vs-dio`. It does **not**
depend on the monorepo (`vs-core`, orchestrator configs, or `modules/vecow`).

## Layout

```
standalone_dio/
├── config.yaml              # Own settings (mqtt + dio + handlers)
├── requirements.txt
├── pyproject.toml
├── run_worker.py            # MQTT pin worker (Vecow hardware)
├── run_service.py           # Condition → pin mapping service
├── emit_verdict.py          # One-shot test PASS/FAIL publish
├── vendor/vecow/            # Vendor DLLs + IOConfig
├── tools/
│   ├── pulse_pin.py
│   └── blink.py
└── dio/                     # Python package
    ├── worker.py
    ├── service.py
    ├── dio_controller.py
    ├── handlers/            # Local pin mappers (not plugins/*)
    └── ...
```

## Install

From this folder (any Python 3.10+ venv):

```bat
cd standalone_dio
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Or editable install:

```bat
pip install -e .
```

## Configure

Edit `config.yaml`:

| Key | Purpose |
|-----|---------|
| `mqtt.broker` / `port` | Broker the service and worker use |
| `dio.topics.do` / `di` | Command / input MQTT topics |
| `dio.do_count` / `di_count` | Pin bank sizes |
| `dio.dio_reset_delay` | Pulse auto-reset seconds (worker) |
| `dio.pin_mapping` | Optional override of `dio/handlers/dio_configs.yaml` |
| `handlers` | Handler modules loaded from `dio/handlers/` |

Optional: point at different DLLs with env `VECOW_DLL_DIR` (defaults to `vendor/vecow`).

## Run

**Pin worker** (needs Vecow hardware + MQTT broker):

```bat
python run_worker.py
python run_worker.py --config config.yaml --broker 192.168.1.10
```

**Mapping service** (handlers publish DO commands over MQTT):

```bat
python run_service.py
```

**Emit a test verdict** (starts service briefly, publishes pulse, exits):

```bat
python emit_verdict.py pass
python emit_verdict.py fail
```

**Hardware helpers** (no MQTT):

```bat
python tools\pulse_pin.py 3 --pulse 1.0
python tools\blink.py 0
```

## Architecture

```
emit_verdict / your app
        │ on_verdict(RuleResult)
        ▼
  DioService  ──MQTT dio/output──►  run_worker  ──►  Vecow DO pins
        ▲                              │
        └──── MQTT dio/input ◄─────────┘  (DI polls)
```

Handlers under `dio/handlers/` register with `@register(on=DioCondition.…)` and are
listed in `config.yaml` → `handlers:`.

## Differences from monorepo `vs-dio`

| Monorepo | Standalone |
|----------|------------|
| Depends on `vs-core` | Vendored minimal `RuleResult` in `dio/core.py` |
| Plugins via pip `vs-<id>` | Local modules in `dio/handlers/` |
| DLLs from `modules/vecow` | Bundled `vendor/vecow` |
| Orchestrator profile YAML | Own `config.yaml` only |

This tree can be copied out of the repo and run as its own project.
