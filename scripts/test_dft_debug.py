#!/usr/bin/env python3
"""Debug the DFT demodulator threshold."""
import time, sys, os
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import sounddevice as sd
from modumb.modem.audio_io import AudioInterface
from modumb.modem.modem import Modem
from modumb.modem.afsk import AFSKModulator, AFSKDemodulator
from modumb.datalink.frame import Frame

output_device = int(os.environ.get('MODEM_OUTPUT_DEVICE', '5'))
input_device = int(os.environ.get('MODEM_INPUT_DEVICE', '1'))

dev_info = sd.query_devices(output_device)
sr = int(dev_info['default_samplerate'])
print(f"Sample rate: {sr} Hz")

# Create modulator/demodulator at device sample rate
mod = AFSKModulator(sample_rate=sr)
demod = AFSKDemodulator(sample_rate=sr)

# Create SYN frame and modulate
syn = Frame.create_syn()
frame_bytes = syn.encode()
tx_samples = mod.modulate(frame_bytes) * 0.08

# Add padding
lead = np.zeros(int(0.3 * sr), dtype=np.float32)
trail = np.zeros(int(0.2 * sr), dtype=np.float32)
tx_samples = np.concatenate([lead, tx_samples, trail])

# Start recording
audio = AudioInterface(input_device=input_device, output_device=output_device)
audio.start()
time.sleep(0.2)
audio.clear_receive_buffer()

# Play
sd.play(tx_samples, sr, device=output_device)
sd.wait()
time.sleep(0.5)

# Collect recording
all_samples = []
while True:
    try:
        block = audio._rx_queue.get_nowait()
        all_samples.append(block)
    except:
        break
audio.stop()

if not all_samples:
    print("No audio recorded!")
    sys.exit(1)

recorded = np.concatenate(all_samples)
print(f"Recorded: {len(recorded)} samples, RMS={np.sqrt(np.mean(recorded**2)):.4f}, Peak={np.max(np.abs(recorded)):.4f}")

# Find signal start
base_offset = demod.find_signal_start(recorded)
print(f"Signal start: sample {base_offset}")

# Compute DFT magnitudes from signal start
spb = demod.samples_per_bit
print(f"Samples per bit: {spb}")

# Try offset = base_offset (aligned to signal start)
offset = base_offset
mark_mags, space_mags = demod._dft_magnitudes(recorded, offset)

# Show magnitude info
total = mark_mags + space_mags
mark_ratio = mark_mags / (total + 1e-10)

# Find signal vs silence
signal_threshold = np.max(total) * 0.05
signal_mask = total > signal_threshold
n_signal = np.sum(signal_mask)
print(f"\nTotal bits: {len(mark_mags)}, Signal bits: {n_signal}")
print(f"Max total magnitude: {np.max(total):.2f}")
print(f"Signal threshold: {signal_threshold:.2f}")

if n_signal > 8:
    signal_ratios = mark_ratio[signal_mask]

    # Show distribution
    print(f"\nMark ratio distribution (signal bits only):")
    print(f"  Min:    {np.min(signal_ratios):.4f}")
    print(f"  25th:   {np.percentile(signal_ratios, 25):.4f}")
    print(f"  Median: {np.median(signal_ratios):.4f}")
    print(f"  75th:   {np.percentile(signal_ratios, 75):.4f}")
    print(f"  Max:    {np.max(signal_ratios):.4f}")
    print(f"  Mean:   {np.mean(signal_ratios):.4f}")

    # Cluster analysis
    sorted_ratios = np.sort(signal_ratios)
    n = len(sorted_ratios)
    bottom25 = sorted_ratios[:max(1, n // 4)]
    top25 = sorted_ratios[-max(1, n // 4):]
    print(f"\n  Bottom 25% mean: {np.mean(bottom25):.4f} (space cluster)")
    print(f"  Top 25% mean:    {np.mean(top25):.4f} (mark cluster)")
    print(f"  Midpoint:        {(np.mean(bottom25) + np.mean(top25)) / 2:.4f}")

    # K-means-style threshold
    threshold = np.median(signal_ratios)
    for _ in range(20):
        low = signal_ratios[signal_ratios < threshold]
        high = signal_ratios[signal_ratios >= threshold]
        if len(low) == 0 or len(high) == 0:
            break
        new_threshold = (np.mean(low) + np.mean(high)) / 2
        if abs(new_threshold - threshold) < 1e-6:
            break
        threshold = new_threshold
    print(f"  K-means threshold: {threshold:.4f}")
    print(f"  Bits marked as '1' (mark): {np.sum(signal_ratios > threshold)}")
    print(f"  Bits marked as '0' (space): {np.sum(signal_ratios <= threshold)}")

    # Show expected: SYN frame = 25 bytes, 200 bits, ~86 mark + ~114 space
    print(f"\n  Expected: ~86 mark + ~114 space = 200 total signal bits")

    # Histogram
    print(f"\n  Histogram of mark ratios (signal bits):")
    bins = np.linspace(0, 1, 21)
    hist, _ = np.histogram(signal_ratios, bins=bins)
    for i, count in enumerate(hist):
        bar = '#' * count
        print(f"    {bins[i]:.2f}-{bins[i+1]:.2f}: {bar} ({count})")

    # Demodulate with k-means threshold
    bits = [1 if r > threshold else 0 for r in mark_ratio]
    data = demod._bits_to_bytes(bits)
    print(f"\nDemodulated ({len(data)} bytes):")
    print(f"  {data[:30].hex()}")
    aa_count = sum(1 for b in data[:20] if b == 0xAA)
    print(f"  Preamble 0xAA count: {aa_count}/16")

    # Try to decode frame
    frame = Frame.decode(data)
    if frame:
        print(f"  *** FRAME DECODED: {frame} ***")
    else:
        print(f"  Frame decode failed")
