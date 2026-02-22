#!/usr/bin/env python3
"""Test DELL with DirectSound."""
import time
import numpy as np
import sounddevice as sd

# Test device 16 (DirectSound)
device = 16
dev_info = sd.query_devices(device)
print(f"Testing device {device}: {dev_info['name']}")
print(f"  Host API: {sd.query_hostapis(dev_info['hostapi'])['name']}")
print(f"  Sample rate: {dev_info['default_samplerate']}")
print()

# Generate 1-second tone at native sample rate
sr = int(dev_info['default_samplerate'])
duration = 1.0
t = np.linspace(0, duration, int(sr * duration), dtype=np.float32)
tone = 0.5 * np.sin(2 * np.pi * 1000 * t)

print(f"Playing {duration}s tone...")
start = time.time()
sd.play(tone, sr, device=device)
sd.wait()
elapsed = time.time() - start
print(f"Playback took: {elapsed:.2f}s")

if abs(elapsed - duration) < 1.0:
    print("SUCCESS!")
else:
    print(f"FAILED - expected ~{duration}s")
