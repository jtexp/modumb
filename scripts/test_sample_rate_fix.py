#!/usr/bin/env python3
"""Verify that the sample rate auto-detection fix works.

Tests:
1. AudioInterface detects native device sample rate
2. Modem uses the detected rate for modulator/demodulator
3. Audio plays at the correct speed (1s of audio = 1s playback)
"""
import time
import sys
import os
import numpy as np

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import sounddevice as sd
from modumb.modem.audio_io import AudioInterface
from modumb.modem.modem import Modem

output_device = int(os.environ.get('MODEM_OUTPUT_DEVICE', '5'))
input_device = int(os.environ.get('MODEM_INPUT_DEVICE', '1'))

print("=== Sample Rate Fix Verification ===\n")

# Step 1: Check device info
dev_info = sd.query_devices(output_device)
print(f"Output device {output_device}: {dev_info['name']}")
print(f"  Native sample rate: {int(dev_info['default_samplerate'])} Hz")

dev_info_in = sd.query_devices(input_device)
print(f"Input device {input_device}: {dev_info_in['name']}")
print(f"  Native sample rate: {int(dev_info_in['default_samplerate'])} Hz")
print()

# Step 2: Create AudioInterface and verify rate detection
audio = AudioInterface(
    sample_rate=48000,  # Request 48000
    input_device=input_device,
    output_device=output_device,
)
print(f"AudioInterface requested: 48000 Hz")
print(f"AudioInterface actual:    {audio.sample_rate} Hz")
print()

# Step 3: Create Modem and verify it uses the detected rate
modem = Modem(
    input_device=input_device,
    output_device=output_device,
)
print(f"Modem sample rate:     {modem.sample_rate} Hz")
print(f"Modulator sample rate: {modem.modulator.sample_rate} Hz")
print(f"Demodulator sample rate: {modem.demodulator.sample_rate} Hz")
print(f"Samples per bit:       {modem.modulator.samples_per_bit}")
print()

# Step 4: Test playback timing
sr = audio.sample_rate
n_samples = sr  # Exactly 1 second at the native rate
t = np.arange(n_samples, dtype=np.float32) / sr
tone = (0.3 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)

print(f"Playing {n_samples} samples at {sr} Hz (should be ~1.0s)...")
start = time.time()
sd.play(tone, sr, device=output_device)
sd.wait()
elapsed = time.time() - start
print(f"Actual duration: {elapsed:.3f}s")

if 0.9 <= elapsed <= 1.15:
    print("=> PASS: Playback timing correct!")
else:
    print(f"=> FAIL: Expected ~1.0s, got {elapsed:.3f}s")

print()

# Step 5: Quick modulation/playback test
print("Playing a short modem frame (SYN)...")
from modumb.datalink.frame import Frame
syn = Frame.create_syn()
frame_data = syn.encode()
samples = modem.modulator.modulate(frame_data)

# Add silence padding
lead = np.zeros(int(0.1 * sr), dtype=np.float32)
trail = np.zeros(int(0.1 * sr), dtype=np.float32)
samples_padded = np.concatenate([lead, samples, trail])

duration = len(samples_padded) / sr
print(f"  Frame: {len(frame_data)} bytes -> {len(samples_padded)} samples ({duration:.2f}s)")

start = time.time()
sd.play(samples_padded.astype(np.float32), sr, device=output_device)
sd.wait()
elapsed = time.time() - start
print(f"  Playback: {elapsed:.3f}s (expected ~{duration:.2f}s)")

if abs(elapsed - duration) < 0.2:
    print("=> PASS: Frame plays at correct speed!")
else:
    print(f"=> FAIL: Timing off by {abs(elapsed - duration):.3f}s")

print("\n=== Done ===")
