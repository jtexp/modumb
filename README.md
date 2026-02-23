# Modumb

**Browse the web through an audio cable** -- an HTTP proxy over acoustic modem.

```
Machine A (no internet)                    Machine B (has internet)
+--------------+                          +--------------+
| Browser/curl |                          |              |
|   | TCP      |                          |   Internet   |
| LocalProxy   |-- audio cable ---------> | RemoteRelay  |
| :8080        |<-- audio cable ----------|              |
+--------------+                          +--------------+
    modem-proxy                              modem-relay
```

Machine A has no internet but has a sound card. Machine B has internet. Connect them with a 3.5mm audio cable (or just point speakers at microphones). Data flows as AFSK tones at 300 baud (default) or 1200 baud.

## Quick Start

### Install

```bash
git clone https://github.com/your-username/modumb.git
cd modumb
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

### Test with Loopback (no audio hardware)

**Terminal 1 -- Relay (Machine B):**
```bash
modem-relay --mode loopback
```

**Terminal 2 -- Proxy (Machine A):**
```bash
modem-proxy --mode loopback
```

**Terminal 3 -- Browse:**
```bash
curl --proxy http://localhost:8080 http://example.com
```

### Audio Cable Mode (two machines)

**Machine B** (has internet):
```bash
modem-relay --mode cable --baud-rate 1200 -i <mic_device> -o <speaker_device>
```

**Machine A** (no internet):
```bash
modem-proxy --mode cable --baud-rate 1200 -i <mic_device> -o <speaker_device>
curl --proxy http://localhost:8080 http://example.com
```

Use `modem-audio devices` to find device indices.

### Virtual Audio Cable Mode (single Windows machine)

For testing on one machine, use two Virtual Audio Cable (Muzychenko) cables to simulate the two-machine setup:

```bash
# Machine B (relay): output on Cable 2, input from Cable 1
modem-relay --mode cable -o 8 -i 3

# Machine A (proxy): output on Cable 1, input from Cable 2
modem-proxy --mode cable -o 11 -i 5
curl --proxy http://localhost:8080 http://example.com
```

Device indices vary by system -- run `modem-audio devices` to find yours.

### Acoustic Mode (speaker/mic, no cable)

```bash
# Machine B
modem-relay --mode acoustic -i <mic> -o <speaker>

# Machine A
modem-proxy --mode acoustic -i <mic> -o <speaker>
```

Place machines 1-2 meters apart in a quiet room.

## Audio Profiles

| Profile | tx_volume | echo_guard | Use case |
|---------|-----------|------------|----------|
| `acoustic` | 0.08 | 80ms | Speaker -> microphone (over the air) |
| `cable` | 0.5 | 0 | 3.5mm line-out -> line-in or virtual cable |
| `loopback` | 1.0 | 0 | In-memory testing (no audio hardware) |

Set via `--mode` flag or `MODEM_MODE` environment variable.

## How It Works

Browser sends `GET http://example.com/path` to the local proxy on `localhost:8080`. The proxy forwards the full HTTP request over a modem session (AFSK audio). The remote relay receives it, fetches from the real internet via `urllib`, and returns the response back over the modem.

### Protocol Stack

| Layer | Module | Role |
|-------|--------|------|
| Physical | `modem/` | AFSK modulation (1200/2200 Hz), 300/1200 baud, per-device audio streams |
| Data Link | `datalink/` | Framing, preamble sync, HDLC byte stuffing, CRC-16 with 1-2 bit error correction |
| Transport | `transport/` | Stop-and-Wait ARQ, 3-way handshake sessions |
| HTTP | `http/` | HTTP/1.1 client/server over modem session |
| Proxy | `proxy/` | Local HTTP proxy + remote internet relay |

### Frame Format

```
Preamble (16 x 0xAA) | Sync (0x7E 0x7E) | Type (1B) | Seq (2B) | Len (2B) | Payload (0-64B) | CRC-16 (2B)
```

### Duplex Modes

- **Half-duplex** (default for acoustic): one direction at a time, turnaround delay between TX/RX
- **Full-duplex** (default for cable/loopback): simultaneous send/receive on separate cables, faster throughput

Set via `--duplex half` or `--duplex full`, or `MODEM_DUPLEX` environment variable.

### Performance

Measured over Virtual Audio Cable (Muzychenko) on a single Windows machine:

| Page | 300 baud | 1200 baud | Speedup |
|------|----------|-----------|---------|
| example.com (528 B) | 72.5s / 7.3 B/s | 30.2s / 17.5 B/s | 2.4x |
| info.cern.ch (646 B) | 78.3s / 8.2 B/s | 32.9s / 19.7 B/s | 2.4x |

Max payload per frame is 64 bytes. Throughput is limited by stop-and-wait ARQ: each DATA frame requires an ACK before the next is sent. Use `--baud-rate 1200` on both sides for faster transfers over cable or virtual cable.

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
| `MODEM_BAUD_RATE` | Baud rate: `300` (default) or `1200` |
| `MODEM_LOOPBACK` | Enable loopback mode (`1`) |
| `MODEM_AUDIBLE` | Play audio in loopback mode |

## Platform Setup

- **Windows**: Works out of the box
- **macOS**: `brew install portaudio`
- **Linux**: `sudo apt install libportaudio2 portaudio19-dev`
- **WSL2**: Use WSLg (Win11) or `--mode loopback` for testing

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run tests with coverage
pytest tests/ -v --cov=modumb

# E2e test through virtual audio cables (Windows)
python scripts/test_e2e_vac.py small
python scripts/test_e2e_vac.py small --baud-rate 1200
python scripts/test_e2e_vac.py medium
```

## Limitations

- **Slow** -- 300/1200 baud with stop-and-wait ARQ, best for small pages and API responses
- **Max response** -- 1MB default (configurable via `--max-response-size`)
- **HTTPS** -- CONNECT tunneling is implemented but has a known issue with TLS handshakes failing on long sessions over Virtual Audio Cable (works over real audio cables)

## License

MIT
