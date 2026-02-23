# Debugging Journey: Acoustic Modem Git Transport

This document chronicles the issues encountered and solutions discovered while implementing git clone over an acoustic modem using AFSK modulation at 300 baud.

## 1. Echo Suppression for Half-Duplex Operation

### Problem
When transmitting audio, the microphone picks up the speaker output, causing the receiver to decode its own transmission as incoming data.

### Solution
Implemented echo suppression in `audio_io.py`:
- Set `_transmitting` flag during transmission to ignore input
- Clear receive buffer before and after transmission
- Add `_echo_guard_time` (80ms) after transmission ends before accepting input
- Track `_last_tx_end` timestamp to enforce guard period

```python
# During transmission
self._transmitting = True
self.clear_receive_buffer()
sd.play(samples)
sd.wait()
self._transmitting = False
self._last_tx_end = time.time()
self.clear_receive_buffer()
```

## 2. AFSK Demodulation Bit Errors

### Problem
Longer transmissions showed consistent bit errors at specific positions. "HTTP/1.1 200 OK" was being decoded as "HTTP/1.1 600 O" with systematic bit flipping.

### Diagnosis
- Bandpass filter bandwidth was too narrow (200Hz initially)
- Clock drift between transmitter and receiver caused bit timing to shift
- Over long transmissions, bits would drift out of the detection window

### Solution
Increased filter bandwidth progressively in `afsk.py`:
```python
# Original: bandwidth = 200 Hz
# Attempt 1: bandwidth = 300 Hz (helped but not enough)
# Final: bandwidth = 400 Hz (reliable operation)
```

The wider bandwidth allows for more frequency variation and timing drift tolerance while still distinguishing mark (1200Hz) from space (2200Hz).

## 3. Silence Detection Timing

### Problem
`receive_until_silence()` was either:
- Returning too early during inter-frame gaps
- Waiting too long and missing subsequent frames

### Solution
Tuned parameters in `modem.py`:
```python
samples = self.audio.receive_until_silence(
    timeout=timeout,
    min_samples=10000,   # ~200ms minimum (was 50000)
    silence_duration=0.3, # 300ms silence threshold (was 1.0)
)
```

Shorter silence duration allows faster response to new transmissions while still detecting frame boundaries.

## 4. Session Establishment Synchronization

### Problem
Client reported "Connected!" but server's `accept_server_session()` kept returning None. The 3-way handshake (SYN → SYN-ACK → ACK) wasn't completing properly.

### Diagnosis
- Server was receiving its own echo or noise during the handshake
- Timing mismatch: server timeout expired before client's ACK arrived
- Echo guard time was blocking legitimate incoming frames

### Solution
- Reduced echo guard time from 150ms to 80ms
- Reduced turnaround delay from 100ms to 50ms
- These faster timings allow the server to receive the client's ACK before timeout

## 5. Clock Drift and Frame Size

### Problem
Larger frames (256 bytes) consistently failed CRC verification. At 300 baud:
- 256 bytes = 2048 bits = ~6.8 seconds of transmission
- Even 0.1% clock drift = 6.8ms timing error = 2+ bits of drift

### Solution
Reduced maximum payload size in `frame.py`:
```python
MAX_PAYLOAD_SIZE = 64  # Was 256
```

Shorter frames limit the accumulation of clock drift. 64 bytes = ~1.7 seconds, keeping drift under 1 bit.

## 6. ARQ Timeout Tuning

### Problem
Default 3-second ACK timeout was marginal for 300 baud transmissions where a single frame can take 2+ seconds.

### Solution
Increased timeouts in `reliable.py`:
```python
DEFAULT_TIMEOUT = 5.0      # Was 3.0 - longer for 300 baud
DEFAULT_RETRIES = 5        # Was 3 - more attempts for reliability
TURNAROUND_GUARD = 0.1     # Was 0.3 - faster response
```

## 7. Git Smart HTTP Protocol Simplification

### Problem
Git clone failed with "bad object" errors. The pack data was received but corrupted or incomplete.

### Diagnosis
- Client was requesting `side-band-64k` capability
- Server responded with sideband-multiplexed data
- Pack parsing expected raw PACK data but got pkt-line wrapped data

### Solution
Disabled sideband in `smart_http.py`:
```python
# Build capabilities we support
# Note: NOT using side-band-64k to simplify response parsing
caps = ['ofs-delta', 'thin-pack']  # Removed 'multi_ack', 'side-band-64k'
```

Without sideband, the server sends NAK followed by raw PACK data, which is easier to parse.

## 8. CRC Debugging

### Problem
Frames were being rejected with CRC mismatch but it wasn't clear what was corrupted.

### Solution
Added detailed CRC debug output in `frame.py`:
```python
if received_crc != computed_crc:
    print(f'DEBUG FRAME: CRC mismatch: received=0x{received_crc:04x} '
          f'computed=0x{computed_crc:04x} length={length}')
    print(f'DEBUG FRAME: Payload start: {payload[:50].hex()}')
```

This revealed the systematic bit errors that led to the filter bandwidth fix.

## 9. PulseAudio Loopback Configuration

### Prerequisite
For testing without physical audio hardware, PulseAudio virtual loopback was used:
```bash
pactl load-module module-null-sink sink_name=virtual_speaker
pactl load-module module-loopback source=virtual_speaker.monitor
```

This creates a virtual speaker whose output loops back to the microphone input.

## 10. Full-Duplex Audio I/O and VAC Zero-Block Gaps

### Problem
When running in full-duplex mode over Virtual Audio Cable, the PortAudio input callback occasionally delivered blocks of pure zeros during concurrent TX, creating gaps in the captured audio that corrupted demodulation.

### Solution
Replaced zero-block dropping with noise injection in `audio_io.py`:
- Detect zero-amplitude blocks in the input callback
- Replace them with low-level noise instead of dropping them, preserving timing continuity
- Use high-latency `InputStream` settings to reduce dropout frequency

## 11. DFT Demodulator Offset Alignment Bug (HTTPS seq=8 Failure)

### Problem
HTTPS E2E tests over VAC consistently failed at frame seq=8. The demodulator saw mark (1200 Hz) everywhere instead of distinguishing mark from space, causing CRC failures. HTTP tests passed because they completed within ~7 frames before the issue manifested.

### Diagnosis Journey

**Phase 1: Rule out raw modem degradation.** Created `diag_vac_degradation.py` with 5 test phases (cable-isolated, alternating, concurrent, stream-reset). Sent 100 fixed DATA frames across all phases. Result: 100/100 PASS with score=22, mark_ratio=0.39, confidence=100%. The VAC cables, concurrent I/O, GIL contention, and stream state accumulation were NOT the cause.

**Phase 2: Identify the trigger.** Created `diag_vac_degradation2.py` with 7 protocol-level tests. T1 (timing sweep) and T2 (ARQ simulation) passed. T3 (payload patterns) found the smoking gun: frames with `all_zero` payload (0x00 x 64) failed 8/9 times, while `fixed` payload passed 10/10. The all-zero payload contains 512 consecutive space-frequency bits.

**Phase 3: Root cause analysis.** Ran targeted analysis on saved WAV files from T3 failures:

```
Best envelope offset: 2164 (score=6)
DFT at envelope offset 2164: score=0 (preamble decoded as 0x55, bit-inverted!)
DFT at offset 2124: score=22, frame decodes perfectly
Delta: -40 samples = exactly 1 bit period at 1200 baud
```

**Two failure modes identified:**

1. **DFT offset mismatch**: The `demodulate()` method found `best_offset` by scoring with the envelope detector (IIR bandpass filters). Envelope detection has group delay from the IIR filters, so its optimal bit alignment is shifted by ~1 bit period relative to DFT's optimal alignment. All three demodulation strategies (envelope, DFT+clock recovery, DFT simple) shared this same envelope-optimized offset. When DFT used it, preamble bytes 0xAA decoded as 0x55 (every bit inverted).

2. **IIR filter settling**: After 512 consecutive space-frequency bits (the all-zero payload), the mark bandpass filter state decayed to near-zero. When mark bits appeared in the CRC at frame end, the filter couldn't respond fast enough, causing 1-2 bit errors in the last payload byte and CRC.

### Fix (commit 8e56722)
Two changes in `afsk.py`:

1. **DFT-specific offset search**: After finding the envelope's `best_offset`, scan +/-1.5 bit periods around it using `_demodulate_dft()` + `_score_alignment()` to find the DFT-optimal offset independently:
```python
dft_search_start = max(0, best_offset - spb * 3 // 2)
dft_search_end = min(len(samples) - spb * 8, best_offset + spb * 3 // 2)
for off in range(dft_search_start, dft_search_end, dft_step):
    d = self._demodulate_dft(samples, off)
    s = self._score_alignment(d)
    if s > dft_best_score:
        dft_best_score = s
        dft_offset = off
```

2. **Candidate reordering**: DFT strategies listed before envelope in the candidate list. Since Python's `sort()` is stable, DFT wins ties. DFT bit decisions are stateless (no filter memory), so they're immune to IIR settling drift that can corrupt envelope results after long same-frequency runs.

### Verification
- All 8 saved all-zero WAV files that previously failed now decode correctly
- Added frame-level roundtrip unit tests: all_zero, all_one, alternating, and sequential payloads at both 300 and 1200 baud (8 new tests, all pass)
- HTTPS E2E test now passes through seq=9 (previously failed at seq=8)

## 12. HTTPS TX/RX Collision at seq=10

### Problem
After fixing the DFT offset bug, HTTPS E2E tests advance past seq=8 but now fail at seq=10. Both proxy and relay attempt to transmit simultaneously on separate VAC cables. The relay's noise probe captures the proxy's TX signal (`noise_rms=0.3537`), and the subsequent receive gets garbled data with CRC=0x0000.

This occurs in both half-duplex and full-duplex modes. The CONNECT tunnel protocol has bidirectional data flow (client->server TLS data and server->client TLS data), and the stop-and-wait ARQ on each side can trigger TX at overlapping times.

### Status
Open issue (modumb-40t). The demodulation layer is working correctly — the failure is in protocol-level TX/RX timing coordination.

## Key Lessons Learned

1. **Half-duplex timing is critical** - Echo suppression, guard times, and turnaround delays must be carefully balanced.

2. **Filter bandwidth affects reliability** - Wider filters handle timing variations better but must not overlap between frequencies.

3. **Shorter frames are more reliable** - Clock drift accumulates over time; limit frame duration.

4. **Retransmission saves the day** - ARQ with CRC verification handles transient errors gracefully.

5. **Debug output is essential** - Hex dumps of received data and CRC values made systematic errors visible.

6. **Protocol simplification helps** - Disabling advanced features (sideband) made debugging easier.

7. **IIR filters have hidden state coupling** - Envelope detection IIR filters introduce group delay that shifts optimal bit alignment. Sharing one alignment offset across fundamentally different strategies (stateful IIR vs stateless DFT) creates payload-dependent failures that only manifest with extreme bit patterns.

8. **Systematic elimination beats guessing** - The 5-phase raw modem diagnostic ruled out an entire category of causes (cable, driver, GIL, stream state) in one 100-frame run. The 7-test protocol diagnostic then pinpointed the exact trigger (all-zero payload). Investing in diagnostic tooling pays for itself.

9. **Unit tests need adversarial payloads** - Standard test data (ASCII text, mixed bytes) exercises the "easy middle" of the demodulator. Edge cases like all-zero (512 consecutive space bits) or all-one (512 consecutive mark bits) stress filter settling and adaptive thresholds in ways normal data never does.

10. **Bidirectional protocols need TX/RX coordination** - Stop-and-wait ARQ works well for request/response patterns but breaks down when both sides have independent data to send (CONNECT tunneling). Each side's ARQ loop can trigger TX at overlapping times, causing collisions even on separate physical cables.

## Final Configuration Summary

| Parameter | Value | File |
|-----------|-------|------|
| Baud rate | 300 | afsk.py |
| Mark frequency | 1200 Hz | afsk.py |
| Space frequency | 2200 Hz | afsk.py |
| Filter bandwidth | 400 Hz | afsk.py |
| Max payload size | 64 bytes | frame.py |
| ARQ timeout | 5.0 seconds | reliable.py |
| ARQ retries | 5 | reliable.py |
| Echo guard time | 80 ms | audio_io.py |
| Turnaround delay | 50 ms | modem.py |
| Silence detection | 300 ms | modem.py |
