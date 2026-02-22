# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Modumb is an acoustic modem that enables `git clone` over sound waves. It uses AFSK (Audio Frequency Shift Keying) at 300 baud (~37.5 bytes/sec) with a full 5-layer protocol stack modeled on OSI.

## Build & Test Commands

```bash
# Install (editable)
pip install -e .

# Run all unit tests
pytest tests/ -v

# Run tests with coverage
pytest tests/ -v --cov=modumb

# Run a single test file
pytest tests/test_afsk.py -v

# Run a single test by name
pytest tests/test_frame.py -v -k "test_roundtrip"

# End-to-end loopback test (creates repo, starts server, clones via modem)
./scripts/run-loopback-test.sh

# AFSK modem layer test
./scripts/test-audio-loopback.sh
```

On Windows (from WSL2), use the Windows venv Python for scripts needing audio:
```bash
.venv/Scripts/python.exe "C:/Users/John/modumb/scripts/<script>.py"
```

## Architecture

Five-layer protocol stack, bottom to top:

| Layer | Module | Role |
|-------|--------|------|
| 1. Physical | `modem/afsk.py`, `modem/audio_io.py`, `modem/modem.py` | AFSK modulation (1200/2200 Hz), audio I/O via sounddevice |
| 2. Data Link | `datalink/frame.py`, `datalink/framer.py` | Framing with preamble + sync, HDLC byte stuffing, CRC-16-CCITT. Max 64-byte payload (clock drift limit) |
| 3. Transport | `transport/reliable.py`, `transport/session.py` | Stop-and-Wait ARQ (5s timeout, 5 retries), 3-way handshake session management |
| 4. HTTP | `http/client.py`, `http/server.py`, `http/pktline.py` | HTTP/1.1 over modem, plus Git pkt-line format |
| 5. Git | `git/remote_helper.py`, `git/smart_http.py` | Git remote helper for `modem://` URLs, Git Smart HTTP protocol |

All source is under `src/modumb/`. Each layer's `__init__.py` uses `__getattr__` for lazy imports (defers numpy/scipy loading).

## Data Flow

`git clone modem://audio/repo` triggers:
1. Git invokes `git-remote-modem` (remote helper protocol)
2. Remote helper builds the full stack: Modem → Framer → ReliableTransport → Session → HttpClient → GitSmartHttpClient
3. Session does 3-way handshake (SYN/SYN-ACK/ACK)
4. HTTP requests flow down through transport → framing → AFSK → speaker
5. Server receives audio → demodulates → decodes frames → parses HTTP → runs git-upload-pack → responds back through the stack

## Entry Points

Three CLI commands defined in `pyproject.toml [project.scripts]` with shell wrappers in `bin/`:
- **git-remote-modem** → `modumb.git.remote_helper:main` — called by git for `modem://` URLs
- **modem-git-server** → `modumb.http.server:main` — serves git repos over acoustic modem
- **modem-audio** → `modumb.cli:main` — audio device listing and testing (`devices`, `test`)

## Key Environment Variables

| Variable | Purpose |
|----------|---------|
| `MODEM_LOOPBACK` | `1` to bypass real audio (uses in-memory buffer) |
| `MODEM_AUDIBLE` | Play audio even in loopback mode |
| `MODEM_INPUT_DEVICE` / `MODEM_OUTPUT_DEVICE` | Audio device indices |
| `MODEM_TX_VOLUME` | Transmit volume 0.0–1.0 (default 0.08) |
| `PULSE_SERVER` | PulseAudio server address for WSL2 |

## Critical Parameters

- **Mark/Space frequencies**: 1200 Hz / 2200 Hz (Bell 202 style)
- **Baud rate**: 300
- **Sample rate**: 48000 Hz (auto-adjusts to device native rate)
- **Max frame payload**: 64 bytes (larger causes bit errors from clock drift)
- **ARQ timeout**: 5 seconds
- **Echo guard**: 80ms post-TX silence (half-duplex)
- **Filter bandwidth**: 400 Hz (tuned for clock drift tolerance)

## Frame Format

```
Preamble (16 × 0xAA) | Sync (0x7E 0x7E) | Type (1B) | Seq (1B) | Len (1B) | Payload | CRC-16 (2B)
```

Frame types: DATA, ACK, NAK, SYN, SYN-ACK, FIN, RST.

## Platform Notes

- **Windows**: Works out of the box with sounddevice
- **Linux/WSL2**: Requires `libportaudio2 portaudio19-dev`
- **WSL2 audio**: Use WSLg (Win11), PulseAudio, or `MODEM_LOOPBACK=1`
- **macOS**: Requires `brew install portaudio` and microphone permission
