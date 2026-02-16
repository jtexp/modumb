# Known Issues

## ALSA Underrun Errors

### Description
Occasional ALSA buffer underrun errors occur during audio transmission:
```
ALSA lib pcm.c:8740:(snd_pcm_recover) underrun occurred
```

### Impact
- **Low**: The ARQ retransmission mechanism handles frames corrupted by underruns
- Frames that fail CRC verification due to underruns are automatically retransmitted
- Git clone operations complete successfully despite occasional underruns

### Root Cause
The underrun occurs when the audio playback buffer empties before new samples are written. This can happen due to:
- System load causing delays in audio processing
- PulseAudio/ALSA buffer management timing
- Context switches during transmission

### Attempted Fixes
Adding latency settings to `sd.play()` and `sd.InputStream`:
```python
sd.play(samples, sample_rate, latency='high')  # or latency=0.1
```

**Result**: This caused consistent CRC corruption at the end of frames. The added latency appears to interfere with the half-duplex timing and frame boundary detection, corrupting the final bytes (including CRC) of each transmission.

### Current Workaround
The system operates without explicit latency settings. Underruns are handled by:
1. CRC-16 verification detects corrupted frames
2. ARQ protocol retransmits failed frames (up to 5 retries)
3. Stop-and-Wait ensures reliable delivery

### Potential Future Solutions
- Investigate callback-based audio output instead of `sd.play()`
- Pre-buffer audio samples before transmission
- Adjust PulseAudio configuration for lower latency
- Use a dedicated real-time audio thread
