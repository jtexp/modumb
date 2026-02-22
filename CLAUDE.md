# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Modumb is an HTTP proxy over acoustic modem. Machine A (no internet) runs a local proxy; Machine B (has internet) runs a relay. They communicate over AFSK (Audio Frequency Shift Keying) at 300 baud via speaker/mic or audio cable.

## Python Environment

**We develop in WSL2 but always run Python via the Windows venv.** Never install to WSL system Python.

```bash
# All commands go through the Windows venv Python:
PY="/mnt/c/Users/John/modumb/.venv/Scripts/python.exe"

# Install (editable)
$PY -m pip install -e ".[dev]"

# Run all unit tests
$PY -m pytest tests/ -v

# Run tests with coverage
$PY -m pytest tests/ -v --cov=modumb

# Run a single test file
$PY -m pytest tests/test_profiles.py -v

# Run a single test by name
$PY -m pytest tests/test_proxy.py -v -k "test_connect_returns_501"
```

For scripts needing Windows audio devices:
```bash
$PY "C:/Users/John/modumb/scripts/<script>.py"
```

## Architecture

Five-layer protocol stack (layers 1-4), plus proxy layer on top:

| Layer | Module | Role |
|-------|--------|------|
| 1. Physical | `modem/afsk.py`, `modem/audio_io.py`, `modem/modem.py`, `modem/profiles.py` | AFSK modulation (1200/2200 Hz), audio I/O, audio profiles (acoustic/cable/loopback) |
| 2. Data Link | `datalink/frame.py`, `datalink/framer.py` | Framing with preamble + sync, HDLC byte stuffing, CRC-16-CCITT. Max 64-byte payload |
| 3. Transport | `transport/reliable.py`, `transport/session.py` | Stop-and-Wait ARQ (5s timeout, 5 retries), 3-way handshake session management |
| 4. HTTP | `http/client.py`, `http/server.py` | HTTP/1.1 client/server over modem session |
| 5. Proxy | `proxy/local_proxy.py`, `proxy/remote_proxy.py`, `proxy/config.py` | Local HTTP proxy (Machine A) and remote relay (Machine B) |

All source is under `src/modumb/`. Each layer's `__init__.py` uses `__getattr__` for lazy imports (defers numpy/scipy loading).

## Data Flow

```
Browser → LocalProxy(:8080) → modem session → RemoteRelay → urllib → Internet
                              ← modem session ←            ← response
```

1. Browser sends `GET http://example.com/path` to LocalProxy on localhost:8080
2. LocalProxy forwards the full HTTP request over the modem session via HttpClient
3. RemoteRelay receives it via HttpServer, fetches from the real internet via `urllib`
4. Response flows back: RemoteRelay → modem → LocalProxy → browser

## Entry Points

Three CLI commands defined in `pyproject.toml [project.scripts]` with shell wrappers in `bin/`:
- **modem-proxy** → `modumb.proxy.local_proxy:main` — Machine A local HTTP proxy
- **modem-relay** → `modumb.proxy.remote_proxy:main` — Machine B internet relay
- **modem-audio** → `modumb.cli:main` — audio device listing and testing (`devices`, `test`)

## Key Environment Variables

| Variable | Purpose |
|----------|---------|
| `MODEM_MODE` | `acoustic`, `cable`, or `loopback` (selects audio profile) |
| `MODEM_LOOPBACK` | `1` to bypass real audio (uses in-memory buffer) |
| `MODEM_AUDIBLE` | Play audio even in loopback mode |
| `MODEM_INPUT_DEVICE` / `MODEM_OUTPUT_DEVICE` | Audio device indices |
| `MODEM_TX_VOLUME` | Transmit volume 0.0–1.0 (overrides profile default) |
| `PULSE_SERVER` | PulseAudio server address for WSL2 |

## Audio Profiles

| Profile | tx_volume | echo_guard | lead_silence | hdmi_wake | Use case |
|---------|-----------|------------|--------------|-----------|----------|
| `acoustic` | 0.08 | 80ms | 300ms | yes | Speaker → microphone |
| `cable` | 0.5 | 0 | 100ms | no | 3.5mm audio cable |
| `loopback` | 1.0 | 0 | 0 | no | In-memory testing |

## Critical Parameters

- **Mark/Space frequencies**: 1200 Hz / 2200 Hz (Bell 202 style)
- **Baud rate**: 300
- **Sample rate**: 48000 Hz (auto-adjusts to device native rate)
- **Max frame payload**: 64 bytes (larger causes bit errors from clock drift)
- **ARQ timeout**: 5 seconds
- **Echo guard**: 80ms post-TX silence (acoustic mode, half-duplex)
- **Filter bandwidth**: 400 Hz (tuned for clock drift tolerance)

## Frame Format

```
Preamble (16 × 0xAA) | Sync (0x7E 0x7E) | Type (1B) | Seq (1B) | Len (1B) | Payload | CRC-16 (2B)
```

Frame types: DATA, ACK, NAK, SYN, SYN-ACK, FIN, RST.

## Platform Notes

- **Windows**: Works out of the box with sounddevice
- **Linux/WSL2**: Requires `libportaudio2 portaudio19-dev`
- **WSL2 audio**: Use WSLg (Win11), PulseAudio, or `--mode loopback`
- **macOS**: Requires `brew install portaudio` and microphone permission
