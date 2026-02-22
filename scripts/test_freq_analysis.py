#!/usr/bin/env python3
"""Analyze frequency content of recorded modem signal.

Plays pure mark (1200 Hz) and space (2200 Hz) tones, records them,
and checks if the demodulator can distinguish them. Also does FFT
analysis to verify frequency content.
"""
import time
import sys
import os
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import sounddevice as sd
from modumb.modem.audio_io import AudioInterface

output_device = int(os.environ.get('MODEM_OUTPUT_DEVICE', '5'))
input_device = int(os.environ.get('MODEM_INPUT_DEVICE', '1'))

# Get native sample rate
dev_info = sd.query_devices(output_device)
sr = int(dev_info['default_samplerate'])
print(f"Sample rate: {sr} Hz")
print(f"Output: device {output_device} ({dev_info['name']})")
dev_info_in = sd.query_devices(input_device)
print(f"Input: device {input_device} ({dev_info_in['name']})")
print()

# Create audio interface for recording
audio = AudioInterface(input_device=input_device, output_device=output_device)
audio.start()
time.sleep(0.3)
audio.clear_receive_buffer()

VOLUME = 0.05

# Test 1: Play 1200 Hz (mark) tone
print("=== Test 1: 1200 Hz Mark Tone ===")
duration = 0.5
t = np.arange(int(sr * duration), dtype=np.float32) / sr
mark_tone = (VOLUME * np.sin(2 * np.pi * 1200 * t)).astype(np.float32)
# Add silence padding
lead = np.zeros(int(0.2 * sr), dtype=np.float32)
trail = np.zeros(int(0.3 * sr), dtype=np.float32)
signal = np.concatenate([lead, mark_tone, trail])

sd.play(signal, sr, device=output_device)
sd.wait()
time.sleep(0.3)

# Read recorded audio
samples = []
while True:
    try:
        block = audio._rx_queue.get_nowait()
        samples.append(block)
    except:
        break

if samples:
    recorded = np.concatenate(samples)
    rms = np.sqrt(np.mean(recorded ** 2))
    peak = np.max(np.abs(recorded))
    print(f"  Recorded: {len(recorded)} samples, RMS={rms:.4f}, Peak={peak:.4f}")

    # Find the active portion (where signal is present)
    threshold = peak * 0.3
    active = np.where(np.abs(recorded) > threshold)[0]
    if len(active) > 0:
        start_idx = max(0, active[0] - 1000)
        end_idx = min(len(recorded), active[-1] + 1000)
        active_signal = recorded[start_idx:end_idx]

        # FFT analysis of active portion
        fft = np.fft.rfft(active_signal)
        freqs = np.fft.rfftfreq(len(active_signal), 1/sr)
        magnitudes = np.abs(fft)

        # Find peak frequency
        peak_idx = np.argmax(magnitudes[1:]) + 1  # Skip DC
        peak_freq = freqs[peak_idx]
        print(f"  Peak frequency: {peak_freq:.1f} Hz (expected 1200 Hz)")

        # Show top 5 frequencies
        top_indices = np.argsort(magnitudes[1:])[-5:][::-1] + 1
        print(f"  Top frequencies:")
        for idx in top_indices:
            print(f"    {freqs[idx]:.1f} Hz  (magnitude: {magnitudes[idx]:.1f})")

        # Check energy in mark vs space bands
        mark_band = (freqs >= 1000) & (freqs <= 1400)
        space_band = (freqs >= 2000) & (freqs <= 2400)
        mark_energy = np.sum(magnitudes[mark_band] ** 2)
        space_energy = np.sum(magnitudes[space_band] ** 2)
        print(f"  Mark band (1000-1400 Hz) energy: {mark_energy:.1f}")
        print(f"  Space band (2000-2400 Hz) energy: {space_energy:.1f}")
        print(f"  Mark/Space ratio: {mark_energy/max(space_energy, 0.001):.1f}x")
else:
    print("  No audio recorded!")

print()
time.sleep(0.5)
audio.clear_receive_buffer()

# Test 2: Play 2200 Hz (space) tone
print("=== Test 2: 2200 Hz Space Tone ===")
space_tone = (VOLUME * np.sin(2 * np.pi * 2200 * t)).astype(np.float32)
signal = np.concatenate([lead, space_tone, trail])

sd.play(signal, sr, device=output_device)
sd.wait()
time.sleep(0.3)

samples = []
while True:
    try:
        block = audio._rx_queue.get_nowait()
        samples.append(block)
    except:
        break

if samples:
    recorded = np.concatenate(samples)
    rms = np.sqrt(np.mean(recorded ** 2))
    peak = np.max(np.abs(recorded))
    print(f"  Recorded: {len(recorded)} samples, RMS={rms:.4f}, Peak={peak:.4f}")

    active = np.where(np.abs(recorded) > peak * 0.3)[0]
    if len(active) > 0:
        start_idx = max(0, active[0] - 1000)
        end_idx = min(len(recorded), active[-1] + 1000)
        active_signal = recorded[start_idx:end_idx]

        fft = np.fft.rfft(active_signal)
        freqs = np.fft.rfftfreq(len(active_signal), 1/sr)
        magnitudes = np.abs(fft)

        peak_idx = np.argmax(magnitudes[1:]) + 1
        peak_freq = freqs[peak_idx]
        print(f"  Peak frequency: {peak_freq:.1f} Hz (expected 2200 Hz)")

        top_indices = np.argsort(magnitudes[1:])[-5:][::-1] + 1
        print(f"  Top frequencies:")
        for idx in top_indices:
            print(f"    {freqs[idx]:.1f} Hz  (magnitude: {magnitudes[idx]:.1f})")

        mark_band = (freqs >= 1000) & (freqs <= 1400)
        space_band = (freqs >= 2000) & (freqs <= 2400)
        mark_energy = np.sum(magnitudes[mark_band] ** 2)
        space_energy = np.sum(magnitudes[space_band] ** 2)
        print(f"  Mark band (1000-1400 Hz) energy: {mark_energy:.1f}")
        print(f"  Space band (2000-2400 Hz) energy: {space_energy:.1f}")
        print(f"  Space/Mark ratio: {space_energy/max(mark_energy, 0.001):.1f}x")
else:
    print("  No audio recorded!")

print()
time.sleep(0.5)
audio.clear_receive_buffer()

# Test 3: Play alternating mark/space (like 0xAA preamble)
print("=== Test 3: Alternating Mark/Space (Preamble) ===")
from modumb.modem.afsk import AFSKModulator
mod = AFSKModulator(sample_rate=sr)
preamble_samples = mod.modulate(b'\xAA' * 4)  # 4 bytes of preamble
preamble_samples = preamble_samples * VOLUME
signal = np.concatenate([lead, preamble_samples.astype(np.float32), trail])

sd.play(signal, sr, device=output_device)
sd.wait()
time.sleep(0.3)

samples = []
while True:
    try:
        block = audio._rx_queue.get_nowait()
        samples.append(block)
    except:
        break

if samples:
    recorded = np.concatenate(samples)
    rms = np.sqrt(np.mean(recorded ** 2))
    peak = np.max(np.abs(recorded))
    print(f"  Recorded: {len(recorded)} samples, RMS={rms:.4f}, Peak={peak:.4f}")

    # Try demodulating
    from modumb.modem.afsk import AFSKDemodulator
    demod = AFSKDemodulator(sample_rate=sr)
    result = demod.demodulate(recorded, auto_sync=True)
    print(f"  Demodulated: {len(result)} bytes")
    if len(result) > 0:
        print(f"  Hex: {result[:20].hex()}")
        aa_count = sum(1 for b in result[:10] if b == 0xAA)
        print(f"  0xAA bytes in first 10: {aa_count}")

        # Show binary of first few bytes
        for i, b in enumerate(result[:8]):
            print(f"    byte {i}: 0x{b:02x} = {b:08b}  (expected: 0xaa = 10101010)")
else:
    print("  No audio recorded!")

audio.stop()
print("\n=== Done ===")
