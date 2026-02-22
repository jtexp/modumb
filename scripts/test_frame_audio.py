#!/usr/bin/env python3
"""Test sending audio using default device."""
import time
import numpy as np
import sounddevice as sd

print(f"Default output device: {sd.default.device[1]}")
print(f"Default device info: {sd.query_devices(sd.default.device[1])}")

# Generate a 1-second test tone
duration = 1.0
sr = 44100
t = np.linspace(0, duration, int(sr * duration), dtype=np.float32)
tone = 0.5 * np.sin(2 * np.pi * 1500 * t)

print(f"\nPlaying {duration}s tone using DEFAULT device...")
start = time.time()
sd.play(tone, sr)  # No device specified - use default
sd.wait()
elapsed = time.time() - start
print(f"Playback took: {elapsed:.3f} seconds")

if abs(elapsed - duration) < 0.5:
    print("SUCCESS!")
else:
    print(f"FAILED - expected ~{duration}s, got {elapsed:.1f}s")
