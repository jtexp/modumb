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

## Key Lessons Learned

1. **Half-duplex timing is critical** - Echo suppression, guard times, and turnaround delays must be carefully balanced.

2. **Filter bandwidth affects reliability** - Wider filters handle timing variations better but must not overlap between frequencies.

3. **Shorter frames are more reliable** - Clock drift accumulates over time; limit frame duration.

4. **Retransmission saves the day** - ARQ with CRC verification handles transient errors gracefully.

5. **Debug output is essential** - Hex dumps of received data and CRC values made systematic errors visible.

6. **Protocol simplification helps** - Disabling advanced features (sideband) made debugging easier.

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
