#!/usr/bin/env python3
"""Test HDMI wake-up workaround."""
import sys
import time
sys.path.insert(0, "src")

from modumb.modem.audio_io import AudioInterface
import numpy as np

print("=" * 60)
print("HDMI Wake-up Test")
print("=" * 60)

# Create audio interface (will auto-detect HDMI)
audio = AudioInterface(output_device=5)  # DELL monitor

print(f"Output device: {audio.output_device}")
print(f"HDMI wake-up enabled: {audio._hdmi_wake_enabled}")
print()

# Start the interface
audio.start()

# Generate a 1-second test tone
sr = audio.sample_rate
duration = 1.0
t = np.linspace(0, duration, int(sr * duration), dtype=np.float32)
tone = 0.3 * np.sin(2 * np.pi * 1500 * t)

print("Test 1: First transmission (should trigger wake-up)...")
start = time.time()
audio.transmit(tone, blocking=True)
elapsed = time.time() - start
print(f"  Playback took: {elapsed:.2f}s (expected ~{duration + 0.4:.1f}s with wake-up)")
print()

print("Test 2: Immediate second transmission (should NOT need wake-up)...")
start = time.time()
audio.transmit(tone, blocking=True)
elapsed = time.time() - start
print(f"  Playback took: {elapsed:.2f}s (expected ~{duration:.1f}s)")
print()

print("Test 3: Wait 6 seconds then transmit (should trigger wake-up again)...")
print("  Waiting 6 seconds...")
time.sleep(6)
start = time.time()
audio.transmit(tone, blocking=True)
elapsed = time.time() - start
print(f"  Playback took: {elapsed:.2f}s (expected ~{duration + 0.4:.1f}s with wake-up)")

audio.stop()
print()
print("Done!")
