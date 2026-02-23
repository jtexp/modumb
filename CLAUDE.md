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
Browser -> LocalProxy(:8080) -> modem session -> RemoteRelay -> urllib -> Internet
                              <- modem session <-             <- response
```

1. Browser sends `GET http://example.com/path` to LocalProxy on localhost:8080
2. LocalProxy forwards the full HTTP request over the modem session via HttpClient
3. RemoteRelay receives it via HttpServer, fetches from the real internet via `urllib`
4. Response flows back: RemoteRelay -> modem -> LocalProxy -> browser

## Entry Points

Three CLI commands defined in `pyproject.toml [project.scripts]` with shell wrappers in `bin/`:
- **modem-proxy** -> `modumb.proxy.local_proxy:main` -- Machine A local HTTP proxy
- **modem-relay** -> `modumb.proxy.remote_proxy:main` -- Machine B internet relay
- **modem-audio** -> `modumb.cli:main` -- audio device listing and testing (`devices`, `test`)

## Key Environment Variables

| Variable | Purpose |
|----------|---------|
| `MODEM_MODE` | `acoustic`, `cable`, or `loopback` (selects audio profile) |
| `MODEM_LOOPBACK` | `1` to bypass real audio (uses in-memory buffer) |
| `MODEM_AUDIBLE` | Play audio even in loopback mode |
| `MODEM_INPUT_DEVICE` / `MODEM_OUTPUT_DEVICE` | Audio device indices |
| `MODEM_BAUD_RATE` | Baud rate: 300 (default) or 1200 |
| `MODEM_DUPLEX` | `half` or `full` (default: `full` for cable/loopback, `half` for acoustic — full skips echo suppression and turnaround delays) |
| `MODEM_TX_VOLUME` | Transmit volume 0.0-1.0 (overrides profile default) |
| `PULSE_SERVER` | PulseAudio server address for WSL2 |

## Audio Profiles

| Profile | tx_volume | echo_guard | lead_silence | hdmi_wake | Use case |
|---------|-----------|------------|--------------|-----------|----------|
| `acoustic` | 0.08 | 80ms | 300ms | yes | Speaker -> microphone |
| `cable` | 0.5 | 0 | 100ms | no | 3.5mm audio cable or virtual cable |
| `loopback` | 1.0 | 0 | 0 | no | In-memory testing |

## Critical Parameters

- **Mark/Space frequencies**: 1200 Hz / 2200 Hz (Bell 202 style)
- **Baud rate**: 300 (default), 1200 (via `--baud-rate 1200` or `MODEM_BAUD_RATE=1200`)
- **Sample rate**: 48000 Hz (preferred; auto-falls back to device native rate)
- **Max frame payload**: 64 bytes (larger causes bit errors from clock drift)
- **ARQ timeout**: baud-scaled via `timeout_for_baud()` (~6.7s at 300, ~2.2s at 1200)
- **Echo guard**: 80ms post-TX silence (acoustic mode, half-duplex)
- **Filter bandwidth**: 400 Hz (tuned for clock drift tolerance)
- **Post-handshake delay**: 0.5s (lets remote side process ACK before DATA)

## Frame Format

```
Preamble (16 x 0xAA) | Sync (0x7E 0x7E) | Type (1B) | Seq (2B LE) | Len (2B LE) | Payload | CRC-16 (2B LE)
```

Frame types: DATA, ACK, NAK, SYN, SYN-ACK, FIN, RST.

## Audio I/O Design

- **Input**: per-device `sd.InputStream` with callback, queues blocks to `_rx_queue`
- **Output**: per-device `sd.OutputStream` with blocking `write()` + silence drain
- Each modem instance has independent streams (no global `sd.play()` state conflicts)
- `receive_until_silence()` requires 3 consecutive blocks (~64ms) above threshold before confirming signal (filters cable glitches)
- Demodulator tries 3 strategies (envelope, DFT with clock recovery, simple DFT) and picks the best-scoring one

## Virtual Audio Cable Testing (Windows)

For e2e testing on a single Windows machine, use two Virtual Audio Cable (Muzychenko) cables:

```
Proxy TX -> VAC Cable 1 Out (dev 11) ---> VAC Cable 1 In (dev 3) -> Relay RX
Proxy RX <- VAC Cable 2 In  (dev 5)  <--- VAC Cable 2 Out (dev 8) <- Relay TX
```

Device indices may vary by system -- use `modem-audio devices` to discover them.

### Test scripts

```bash
# Modem-to-modem frame exchange diagnostic
$PY "C:/Users/John/modumb/scripts/diag_modem_exchange.py"

# Single-cable frame roundtrip diagnostic
$PY "C:/Users/John/modumb/scripts/diag_vac_frame.py"

# VAC degradation diagnostic (5-phase, isolates demod degradation root cause)
$PY "C:/Users/John/modumb/scripts/diag_vac_degradation.py" --phases 1,2 --frames 10   # quick
$PY "C:/Users/John/modumb/scripts/diag_vac_degradation.py" --frames 20                 # full

# Protocol-level VAC diagnostic (7 tests: timing, ARQ, payload patterns, etc.)
$PY "C:/Users/John/modumb/scripts/diag_vac_degradation2.py" --tests 1,2,3 --frames 10 # quick
$PY "C:/Users/John/modumb/scripts/diag_vac_degradation2.py" --frames 20               # full
```

### VAC e2e test matrix

**After any change to modem, datalink, transport, HTTP, or proxy layers**, run the
full test matrix if Virtual Audio Cable devices are available. Unit tests alone do
not catch audio timing, clock drift, or real-device I/O regressions.

Cable/VAC tests default to full-duplex. Use `--duplex half` to test half-duplex.

| # | Command | Expected |
|---|---------|----------|
| 1 | `$PY "C:/Users/John/modumb/scripts/test_e2e_vac.py" small --baud-rate 300 --duplex half` | ~73s, ~7 B/s |
| 2 | `$PY "C:/Users/John/modumb/scripts/test_e2e_vac.py" small --baud-rate 1200 --duplex half` | ~30s, ~18 B/s |
| 3 | `$PY "C:/Users/John/modumb/scripts/test_e2e_vac.py" medium --baud-rate 300 --duplex half` | ~78s, ~8 B/s |
| 4 | `$PY "C:/Users/John/modumb/scripts/test_e2e_vac.py" medium --baud-rate 1200 --duplex half` | ~33s, ~20 B/s |
| 5 | `$PY "C:/Users/John/modumb/scripts/test_e2e_vac.py" small --baud-rate 300` | faster than #1 |
| 6 | `$PY "C:/Users/John/modumb/scripts/test_e2e_vac.py" small --baud-rate 1200` | ~27s, ~20 B/s |
| 7 | `$PY "C:/Users/John/modumb/scripts/test_e2e_vac.py" medium --baud-rate 1200` | faster than #4 |
| 8 | `$PY "C:/Users/John/modumb/scripts/test_e2e_vac.py" https --baud-rate 1200 --duplex half` | TLS handshake + response |
| 9 | `$PY "C:/Users/John/modumb/scripts/test_e2e_vac.py" https --baud-rate 1200` | Faster than #8 (full-duplex) |

**Known issue**: HTTPS tests (8, 9) fail at frame seq=10 due to a TX/RX collision
where both proxy and relay transmit simultaneously on separate VAC cables. The relay
receives the frame preamble/sync correctly but CRC bytes are corrupted (0x0000).
This occurs in both half-duplex and full-duplex modes. HTTP tests (1-7) are unaffected.
Tracked in modumb-40t.

**Fixed (2026-02-23)**: The previous seq=8 AFSK demodulation failure was caused by
the DFT demodulator sharing the envelope strategy's bit alignment offset. IIR filter
group delay shifted the optimal DFT offset by exactly 1 bit period, inverting preamble
bits (0xAA->0x55) for payloads with long same-frequency runs (e.g. all-zero TLS data).
Fix: DFT strategies now search for their own optimal offset independently, and DFT is
preferred over envelope on score ties (stateless per-bit, immune to IIR settling drift).

All HTTP tests must pass with zero retransmissions. If short on time, tests 2, 6 are the
minimum (HTTP half-duplex + full-duplex at 1200 baud).

**Important**: Run VAC tests sequentially, never in parallel. They share audio devices
and port 8080. The VAC lock (`scripts/vac_lock.py`) has a TOCTOU race and cannot
prevent concurrent runs launched simultaneously.

## Session Close Protocol

Before finishing a session that touched modem/datalink/transport/HTTP/proxy code,
run the VAC e2e smoke tests. These catch audio timing regressions that unit tests miss.

We run from WSL2 but the Windows venv Python has full access to VAC audio devices,
so e2e tests always work. **Do not skip them** for modem/proxy code changes.

```bash
PY="/mnt/c/Users/John/modumb/.venv/Scripts/python.exe"

# 1. Unit tests (always)
$PY -m pytest tests/ -v

# 2. VAC e2e smoke tests (run sequentially, never in parallel)
$PY "C:/Users/John/modumb/scripts/test_e2e_vac.py" small --baud-rate 1200 --duplex half
$PY "C:/Users/John/modumb/scripts/test_e2e_vac.py" small --baud-rate 1200
# HTTPS tests are known-failing (TX/RX collision at frame seq=10, see modumb-40t)
# $PY "C:/Users/John/modumb/scripts/test_e2e_vac.py" https --baud-rate 1200 --duplex half
# $PY "C:/Users/John/modumb/scripts/test_e2e_vac.py" https --baud-rate 1200
```

All must pass before committing. If only docs/tests/config changed, skip e2e.

## E2E Development Cycle

When iterating on modem/datalink/transport/HTTP/proxy code via Jenkins, follow this
three-phase cycle to balance fast iteration with regression safety:

```
Phase 1: FULL RUN — identify failures
  powershell.exe -ExecutionPolicy Bypass -File C:/Users/John/modumb/$PS run smoke
  # or: trigger (runs full matrix)
  # Observe which tests pass/fail

Phase 2: TARGETED — iterate on failures
  # Re-run only the failing test(s) until they pass
  powershell.exe -ExecutionPolicy Bypass -File C:/Users/John/modumb/$PS run https-1200-half
  powershell.exe -ExecutionPolicy Bypass -File C:/Users/John/modumb/$PS poll master 15 <N>
  # Commit fix, re-run same test, repeat until green

Phase 3: FULL RUN — verify no regressions
  powershell.exe -ExecutionPolicy Bypass -File C:/Users/John/modumb/$PS run smoke
  # All smoke tests must pass before considering the fix complete
```

This avoids wasting time running passing tests during the fix loop (Phase 2) while
still catching regressions at the end (Phase 3). Use `run <id>` for Phase 2 — it's
significantly faster than `run smoke` or `trigger`.

## Issue Tracking

We use `bd` (beads) for lightweight issue tracking with dependency support.

```bash
# List open issues
bd list

# Show ready work (open, no active blockers)
bd ready

# Create an issue
bd create "Title" -d "Description" -t bug -p 2

# Show issue details
bd show <ID>

# Close an issue
bd close <ID> -r "reason"

# Add a comment
bd comments <ID> add "comment text"
```

## Jenkins CI

Jenkins runs locally at http://localhost:8090 as a desktop app (not a service) so it can access Virtual Audio Cable devices for E2E tests. The `modumb` Multibranch Pipeline job scans the local repo at `C:\Users\John\modumb`.

### fetch_jenkins.ps1

PowerShell helper for Jenkins API operations. Run from WSL2 via `powershell.exe` (localhost isn't reachable from WSL2 directly, but `powershell.exe` runs on the Windows side):

```bash
PS=scripts/fetch_jenkins.ps1

# Run specific E2E tests by ID or preset (normal workflow — always trigger manually)
powershell.exe -ExecutionPolicy Bypass -File C:/Users/John/modumb/$PS run small-1200-half
powershell.exe -ExecutionPolicy Bypass -File C:/Users/John/modumb/$PS run small-1200-half,medium-300-half
powershell.exe -ExecutionPolicy Bypass -File C:/Users/John/modumb/$PS run smoke
powershell.exe -ExecutionPolicy Bypass -File C:/Users/John/modumb/$PS run small-1200-half feat-2

# Force-trigger full E2E matrix (explicit only)
powershell.exe -ExecutionPolicy Bypass -File C:/Users/John/modumb/$PS trigger master

# Poll a running build every 15s, print full log when done
powershell.exe -ExecutionPolicy Bypass -File C:/Users/John/modumb/$PS poll master 15

# Poll a specific build number (not lastBuild)
powershell.exe -ExecutionPolicy Bypass -File C:/Users/John/modumb/$PS poll master 15 2

# One-shot status check (shows commit SHA)
powershell.exe -ExecutionPolicy Bypass -File C:/Users/John/modumb/$PS status master

# Fetch console log for a specific or latest build
powershell.exe -ExecutionPolicy Bypass -File C:/Users/John/modumb/$PS lastBuild master
powershell.exe -ExecutionPolicy Bypass -File C:/Users/John/modumb/$PS 3 master

# Scan repo (branch discovery only — does NOT reliably trigger builds)
powershell.exe -ExecutionPolicy Bypass -File C:/Users/John/modumb/$PS scan
```

**Actions**:
- `run <tests> [branch]` — **Primary way to trigger builds.** `<tests>` is comma-separated IDs (e.g. `small-1200-half,medium-300-half`) or a preset (`smoke`, `full`, `none`). Posts `E2E_TESTS=<tests>` via `buildWithParameters`. Resolves to build number. Always use `run` to kick off builds — it triggers immediately and reliably.
- `trigger [branch]` — Force a parameterized build (`E2E_TESTS=full`). Resolves the queue item to a build number. Use for full matrix runs.
- `poll [branch] [interval] [buildNum]` — Poll until build finishes, then print full console log. Shows Git commit SHA when available. Pins to a specific build number after first check.
- `status [branch]` — One-shot status with commit SHA (truncated to 10 chars).
- `scan [branch] [timeout] [commit]` — Rescan repo for branch discovery only. Does **not** reliably trigger builds — use `run` instead.
- `<buildNum> [branch]` / `lastBuild [branch]` — Fetch console text.

**E2E_TESTS parameter** (replaces old `RUN_FULL_MATRIX` boolean):
- Empty (default): auto-detect from changed files, run smoke tests if source changed
- Preset: `smoke` (4 tests), `full` (all 9), `none` (skip E2E)
- Specific IDs: `small-300-half`, `small-1200-half`, `medium-300-half`, `medium-1200-half`, `small-300-full`, `small-1200-full`, `medium-1200-full`, `https-1200-half`, `https-1200-full`

### Jenkins API notes

Jenkins 2.x CSRF protection requires a crumb header on all POST requests. The crumb
is tied to the web session cookie, so you **must reuse the same session** for the crumb
request and the subsequent POST. `fetch_jenkins.ps1` handles this automatically.

**Correct endpoints** (learned the hard way):

| Action | Endpoint | Notes |
|--------|----------|-------|
| Trigger branch build | `POST /job/modumb/job/{branch}/buildWithParameters` | Works with crumb + session. Returns 201. `/build` returns 400 for parameterized jobs. |
| Scan for branches | `POST /job/modumb/indexing/build` | May close the connection after triggering — this is normal, the scan still runs. **Do NOT use `/job/modumb/build`** — returns 403. |
| Build status | `GET /job/modumb/job/{branch}/{num}/api/json` | Returns JSON with `building`, `result`, `number` fields. |
| Console log | `GET /job/modumb/job/{branch}/{num}/consoleText` | Plain text console output. |
| Crumb | `GET /crumbIssuer/api/json` | Returns `crumbRequestField` and `crumb`. Must be used within the same HTTP session. |

**Common gotchas**:
- Always use `run` (not `scan`) to trigger builds. `scan` only does branch discovery and does not reliably start builds.
- `run` returns a build number — pass it to `poll` to track that specific build.
- `poll` with `lastBuild` may return a different build than the one you just triggered (e.g., an auto-scan build). Pass a specific build number to avoid this.
- After triggering a scan, the auto-discovered branches may immediately start building, creating extra builds.
- Auth uses API token (not password): `john:<api-token>` base64-encoded in `Authorization: Basic` header.

### Key Jenkins config

| Item | Value |
|------|-------|
| URL | http://localhost:8090 |
| Job | `modumb` (Multibranch Pipeline, local Git source) |
| Lock | `vac-audio-devices` (serializes E2E tests) |
| Node label | `windows-audio` (Built-In Node) |
| Java flag | `-Dhudson.plugins.git.GitSCM.ALLOW_LOCAL_CHECKOUT=true` (required for local repo) |
| Start command | `java "-Dhudson.plugins.git.GitSCM.ALLOW_LOCAL_CHECKOUT=true" -jar C:\Jenkins\jenkins.war --httpPort=8090` |

## Platform Notes

- **Windows**: Works out of the box with sounddevice
- **Linux/WSL2**: Requires `libportaudio2 portaudio19-dev`
- **WSL2 audio**: Use WSLg (Win11), PulseAudio, or `--mode loopback`
- **macOS**: Requires `brew install portaudio` and microphone permission
