# Modumb

**Browse the web through an audio cable** — an HTTP proxy over acoustic modem.

```
Machine A (no internet)                    Machine B (has internet)
┌──────────────┐                          ┌──────────────┐
│ Browser/curl │                          │              │
│   ↕ TCP      │                          │   Internet   │
│ LocalProxy   │── audio cable ──────────▶│ RemoteRelay  │
│ :8080        │◀── audio cable ──────────│              │
└──────────────┘                          └──────────────┘
    modem-proxy                              modem-relay
```

Machine A has no internet but has a sound card. Machine B has internet. Connect them with a 3.5mm audio cable (or just point speakers at microphones). Data flows as AFSK tones at 300 baud (~37.5 bytes/sec).

## Quick Start

### Install

```bash
git clone https://github.com/your-username/modumb.git
cd modumb
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

### Test with Loopback (no audio hardware)

**Terminal 1 — Relay (Machine B):**
```bash
modem-relay --mode loopback
```

**Terminal 2 — Proxy (Machine A):**
```bash
modem-proxy --mode loopback
```

**Terminal 3 — Browse:**
```bash
curl --proxy http://localhost:8080 http://example.com
```

### Audio Cable Mode (two machines)

**Machine B** (has internet):
```bash
modem-relay --mode cable -i 3 -o 5
```

**Machine A** (no internet):
```bash
modem-proxy --mode cable -i 3 -o 5
curl --proxy http://localhost:8080 http://example.com
```

Use `modem-audio devices` to find device indices.

### Acoustic Mode (speaker/mic, no cable)

```bash
# Machine B
modem-relay --mode acoustic -i 3 -o 5

# Machine A
modem-proxy --mode acoustic -i 3 -o 5
```

Place machines 1-2 meters apart in a quiet room.

## Audio Profiles

| Profile | tx_volume | echo_guard | Use case |
|---------|-----------|------------|----------|
| `acoustic` | 0.08 | 80ms | Speaker → microphone (over the air) |
| `cable` | 0.5 | 0 | 3.5mm line-out → line-in |
| `loopback` | 1.0 | 0 | In-memory testing (no audio hardware) |

Set via `--mode` flag or `MODEM_MODE` environment variable.

## How It Works

Browser sends `GET http://example.com/path` to the local proxy on `localhost:8080`. The proxy forwards the full HTTP request over a modem session (AFSK audio). The remote relay receives it, fetches from the real internet via `urllib`, and returns the response back over the modem.

### Protocol Stack

| Layer | Module | Role |
|-------|--------|------|
| Physical | `modem/` | AFSK modulation (1200/2200 Hz), 300 baud, audio I/O |
| Data Link | `datalink/` | Framing, preamble sync, HDLC stuffing, CRC-16 |
| Transport | `transport/` | Stop-and-Wait ARQ, 3-way handshake sessions |
| HTTP | `http/` | HTTP/1.1 client/server over modem session |
| Proxy | `proxy/` | Local HTTP proxy + remote internet relay |

### Performance

| Metric | Value |
|--------|-------|
| Baud Rate | 300 baud |
| Throughput | ~37.5 bytes/sec |
| Small page (~1KB) | ~30 seconds |
| Larger page (~10KB) | ~5 minutes |

## CLI Commands

| Command | Description |
|---------|-------------|
| `modem-proxy` | Local HTTP proxy (Machine A) |
| `modem-relay` | Remote internet relay (Machine B) |
| `modem-audio devices` | List audio devices |
| `modem-audio test` | Test audio I/O |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `MODEM_MODE` | Audio profile: `acoustic`, `cable`, `loopback` |
| `MODEM_INPUT_DEVICE` | Microphone device index |
| `MODEM_OUTPUT_DEVICE` | Speaker device index |
| `MODEM_TX_VOLUME` | Transmit volume 0.0-1.0 (overrides profile) |
| `MODEM_LOOPBACK` | Enable loopback mode (`1`) |
| `MODEM_AUDIBLE` | Play audio in loopback mode |

## Platform Setup

- **Windows**: Works out of the box
- **macOS**: `brew install portaudio`
- **Linux**: `sudo apt install libportaudio2 portaudio19-dev`
- **WSL2**: Use WSLg (Win11) or `--mode loopback` for testing

## Development

```bash
# Tests
pytest tests/ -v

# Tests with coverage
pytest tests/ -v --cov=modumb
```

## Limitations

- **HTTP only** — HTTPS CONNECT/MITM is Phase 2
- **Slow** — 300 baud, best for small pages and API responses
- **Half-duplex** — one direction at a time
- **Max response** — 1MB default (configurable via `--max-response-size`)

## License

MIT
