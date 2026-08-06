# app_v2 DIO

Self-contained digital I/O MQTT pin bridge for Vecow hardware. It does **not**
depend on the monorepo, and it does **not** interpret verdicts, conditions, or
`y_location` — only pin bit-vectors over MQTT.

## Layout

```
app_v2/
├── config.yaml              # mqtt + dio settings
├── requirements.txt
├── main.py                  # entry: run (default) or emit DO
├── tools/
│   ├── pulse_pin.py
│   └── blink.py
└── Dependencies/
    ├── bridge.py            # MQTT ↔ Vecow bridge
    ├── dio_controller.py    # Vecow DLL wrapper
    ├── config.py            # YAML loader
    └── Vecow/               # DLLs + IOConfig
```

## Install

From this folder (any Python 3.10+ venv):

```bat
cd app_v2
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Configure

Edit `config.yaml`:

| Key | Purpose |
|-----|---------|
| `mqtt.broker` / `port` | MQTT broker |
| `dio.topics.do` / `di` | Command / input topics |
| `dio.banks` / `default_bank` | Hardware banks (1 and/or 2) |
| `dio.block_size` | Pins per bank when splitting a flat vector (default 8) |
| `dio.di_count` | DI pins per bank to poll (0–8) |
| `dio.dio_reset_delay` | Seconds DO pins stay high before auto-low |
| `dio.di_poll_hz` | DI poll rate |

Optional: point at different DLLs with env `VECOW_DLL_DIR` (defaults to `Dependencies/Vecow`).

## MQTT protocol

### Digital output (subscribe `dio.topics.do`)

Required / optional fields only:

```json
{
  "bank": 1,
  "pins": [1, 0, 1, 0, 0, 0, 0, 0],
  "delay": 0.0,
  "capture_time": "2026-04-06 10:00:00",
  "dio_reset_delay": 2.0
}
```

| Field | Required | Meaning |
|-------|----------|---------|
| `pins` | yes | Full **0/1 bit vector** (index = pin number within bank slice) |
| `bank` | no | Bank `1` or `2`. If omitted and `pins` is longer than `block_size`, vector is split across `dio.banks` |
| `delay` | no | Seconds to wait before activating (default `0`) |
| `capture_time` | no | String `YYYY-MM-DD HH:MM:SS`; wait is `max(0, delay - (now - capture_time))` |
| `dio_reset_delay` | no | Seconds to hold high before auto-low (default: `dio.dio_reset_delay` in config) |

A JSON **list** of such objects is also accepted (one command each).

After `delay` (adjusted by `capture_time` if present), the bit vector is applied,
then pins that were **1** return low after `dio_reset_delay`.

### Digital input (publish `dio.topics.di`)

When any polled DI pin **rises**, the bridge publishes a 0/1 mask (1 = rose):

```json
{"bank": 1, "pins": [0, 0, 1, 0, 0, 1, 0, 0]}
```

## Run

**Pin bridge** (needs Vecow hardware + MQTT broker):

```bat
python main.py
python main.py run --broker 192.168.1.10
```

**Emit a test DO command** (broker only; bridge should be running elsewhere):

```bat
python main.py emit 1 0 1 0 0 0 0 0
python main.py emit 1 0 0 0 0 0 0 0 --bank 1 --delay 0.5 --dio-reset-delay 1.0
```

**Hardware helpers** (no MQTT):

```bat
python tools\pulse_pin.py 3 --pulse 1.0
python tools\blink.py 0
```

## Architecture

```
your app / main.py emit
        │ MQTT  {bank?, pins[0/1…], delay?, capture_time?, dio_reset_delay?}
        ▼
  main.py run  ──►  Vecow DO pins
        │
        └──── MQTT  {bank, pins rose as 0/1 mask}  ──►  subscribers
```

This tree can be copied out of the repo and run as its own project.
