#!/usr/bin/env python3
"""Test DFT demodulation with trimmed bit windows to reduce ISI."""
import time, sys, os
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import sounddevice as sd
from modumb.modem.audio_io import AudioInterface
from modumb.modem.afsk import AFSKModulator, AFSKDemodulator
from modumb.datalink.frame import Frame

output_device = int(os.environ.get('MODEM_OUTPUT_DEVICE', '5'))
input_device = int(os.environ.get('MODEM_INPUT_DEVICE', '1'))

dev_info = sd.query_devices(output_device)
sr = int(dev_info['default_samplerate'])
print(f"Sample rate: {sr} Hz")

mod = AFSKModulator(sample_rate=sr)
demod = AFSKDemodulator(sample_rate=sr)
spb = demod.samples_per_bit

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

base_offset = demod.find_signal_start(recorded)
print(f"Signal start: sample {base_offset}")
print(f"Samples per bit: {spb}")

def dft_with_trim(samples, offset, trim_frac):
    """DFT magnitudes with trimmed bit windows."""
    trim_samples = int(spb * trim_frac)
    usable = spb - 2 * trim_samples
    if usable < 10:
        return np.array([]), np.array([])

    num_bits = (len(samples) - offset) // spb
    if num_bits <= 0:
        return np.array([]), np.array([])

    t = np.arange(usable, dtype=np.float64) / sr
    mark_cos = np.cos(2 * np.pi * 1200 * t)
    mark_sin = np.sin(2 * np.pi * 1200 * t)
    space_cos = np.cos(2 * np.pi * 2200 * t)
    space_sin = np.sin(2 * np.pi * 2200 * t)

    mark_mags = np.empty(num_bits)
    space_mags = np.empty(num_bits)

    for i in range(num_bits):
        start = offset + i * spb + trim_samples
        end = start + usable
        if end > len(samples):
            mark_mags = mark_mags[:i]
            space_mags = space_mags[:i]
            break
        chunk = samples[start:end].astype(np.float64)
        mc = np.dot(chunk, mark_cos)
        ms = np.dot(chunk, mark_sin)
        sc = np.dot(chunk, space_cos)
        ss = np.dot(chunk, space_sin)
        mark_mags[i] = mc * mc + ms * ms
        space_mags[i] = sc * sc + ss * ss

    return mark_mags, space_mags

def dft_with_window(samples, offset, window_type='hann'):
    """DFT magnitudes with windowed bit periods."""
    num_bits = (len(samples) - offset) // spb
    if num_bits <= 0:
        return np.array([]), np.array([])

    t = np.arange(spb, dtype=np.float64) / sr
    if window_type == 'hann':
        win = np.hanning(spb)
    elif window_type == 'hamming':
        win = np.hamming(spb)
    else:
        win = np.ones(spb)

    mark_cos = np.cos(2 * np.pi * 1200 * t) * win
    mark_sin = np.sin(2 * np.pi * 1200 * t) * win
    space_cos = np.cos(2 * np.pi * 2200 * t) * win
    space_sin = np.sin(2 * np.pi * 2200 * t) * win

    mark_mags = np.empty(num_bits)
    space_mags = np.empty(num_bits)

    for i in range(num_bits):
        start = offset + i * spb
        end = start + spb
        if end > len(samples):
            mark_mags = mark_mags[:i]
            space_mags = space_mags[:i]
            break
        chunk = samples[start:end].astype(np.float64)
        mc = np.dot(chunk, mark_cos)
        ms = np.dot(chunk, mark_sin)
        sc = np.dot(chunk, space_cos)
        ss = np.dot(chunk, space_sin)
        mark_mags[i] = mc * mc + ms * ms
        space_mags[i] = sc * sc + ss * ss

    return mark_mags, space_mags

def analyze_and_demod(mark_mags, space_mags, label):
    """Analyze distribution and try demodulation."""
    if len(mark_mags) == 0:
        print(f"  No data")
        return

    total = mark_mags + space_mags
    mark_ratio = mark_mags / (total + 1e-10)

    signal_threshold = np.max(total) * 0.05
    signal_mask = total > signal_threshold
    n_signal = np.sum(signal_mask)

    if n_signal <= 8:
        print(f"  Only {n_signal} signal bits")
        return

    signal_ratios = mark_ratio[signal_mask]
    sorted_ratios = np.sort(signal_ratios)
    n = len(sorted_ratios)

    # Separation metric: gap between clusters
    space_center = np.mean(sorted_ratios[:max(1, n // 4)])
    mark_center = np.mean(sorted_ratios[-max(1, n // 4):])
    threshold = (space_center + mark_center) / 2

    # K-means threshold
    km_threshold = np.median(signal_ratios)
    for _ in range(20):
        low = signal_ratios[signal_ratios < km_threshold]
        high = signal_ratios[signal_ratios >= km_threshold]
        if len(low) == 0 or len(high) == 0:
            break
        new_t = (np.mean(low) + np.mean(high)) / 2
        if abs(new_t - km_threshold) < 1e-6:
            break
        km_threshold = new_t

    # Count bits with each threshold
    bits_mid = [1 if r > threshold else 0 for r in mark_ratio]
    bits_km = [1 if r > km_threshold else 0 for r in mark_ratio]

    data_mid = demod._bits_to_bytes(bits_mid)
    data_km = demod._bits_to_bytes(bits_km)

    aa_mid = sum(1 for b in data_mid[:20] if b == 0xAA) if len(data_mid) > 0 else 0
    aa_km = sum(1 for b in data_km[:20] if b == 0xAA) if len(data_km) > 0 else 0

    frame_mid = Frame.decode(data_mid) if len(data_mid) > 0 else None
    frame_km = Frame.decode(data_km) if len(data_km) > 0 else None

    print(f"  Signal bits: {n_signal}")
    print(f"  Space center: {space_center:.4f}, Mark center: {mark_center:.4f}, Gap: {mark_center - space_center:.4f}")
    print(f"  Midpoint threshold: {threshold:.4f} -> AA={aa_mid}/16 {'FRAME OK!' if frame_mid else ''}")
    print(f"  K-means threshold:  {km_threshold:.4f} -> AA={aa_km}/16 {'FRAME OK!' if frame_km else ''}")

    # Also try a fixed 0.5 threshold
    bits_50 = [1 if r > 0.5 else 0 for r in mark_ratio]
    data_50 = demod._bits_to_bytes(bits_50)
    aa_50 = sum(1 for b in data_50[:20] if b == 0xAA) if len(data_50) > 0 else 0
    frame_50 = Frame.decode(data_50) if len(data_50) > 0 else None
    print(f"  Fixed 0.50 thresh:  -> AA={aa_50}/16 {'FRAME OK!' if frame_50 else ''}")

    if frame_mid:
        print(f"  *** DECODED: {frame_mid} ***")
    elif frame_km:
        print(f"  *** DECODED: {frame_km} ***")
    elif frame_50:
        print(f"  *** DECODED: {frame_50} ***")

    # Show histogram
    bins = np.linspace(0, 1, 11)
    hist, _ = np.histogram(signal_ratios, bins=bins)
    print(f"  Histogram: ", end='')
    for i, count in enumerate(hist):
        print(f"{bins[i]:.1f}:{count} ", end='')
    print()

# Try different offset alignments with the best approach
print("\n=== Offset Search ===")
# First find best offset using existing demodulator
coarse_step = max(1, spb // 4)
search_start = max(0, base_offset)
search_end = min(len(recorded) - spb * 8, base_offset + spb * 8 * 16)

best_offset = search_start
best_score = -1

for offset in range(search_start, search_end, coarse_step):
    mark_mags, space_mags = demod._dft_magnitudes(recorded, offset)
    if len(mark_mags) == 0:
        continue
    total = mark_mags + space_mags
    mark_ratio = mark_mags / (total + 1e-10)
    signal_threshold_v = np.max(total) * 0.05
    signal_mask = total > signal_threshold_v
    if np.sum(signal_mask) > 8:
        signal_ratios = mark_ratio[signal_mask]
        sorted_ratios = np.sort(signal_ratios)
        n = len(sorted_ratios)
        sc = np.mean(sorted_ratios[:max(1, n // 4)])
        mc = np.mean(sorted_ratios[-max(1, n // 4):])
        threshold = (sc + mc) / 2
    else:
        threshold = 0.5
    bits = [1 if r > threshold else 0 for r in mark_ratio]
    data = demod._bits_to_bytes(bits)
    score = demod._score_alignment(data)
    if score > best_score:
        best_score = score
        best_offset = offset

# Fine search
if best_score > 0:
    fine_start = max(0, best_offset - coarse_step)
    fine_end = min(len(recorded) - spb * 8, best_offset + coarse_step)
    for offset in range(fine_start, fine_end, max(1, spb // 16)):
        mark_mags, space_mags = demod._dft_magnitudes(recorded, offset)
        if len(mark_mags) == 0:
            continue
        total = mark_mags + space_mags
        mark_ratio = mark_mags / (total + 1e-10)
        signal_threshold_v = np.max(total) * 0.05
        signal_mask = total > signal_threshold_v
        if np.sum(signal_mask) > 8:
            signal_ratios = mark_ratio[signal_mask]
            sorted_ratios = np.sort(signal_ratios)
            n = len(sorted_ratios)
            sc = np.mean(sorted_ratios[:max(1, n // 4)])
            mc = np.mean(sorted_ratios[-max(1, n // 4):])
            threshold = (sc + mc) / 2
        else:
            threshold = 0.5
        bits = [1 if r > threshold else 0 for r in mark_ratio]
        data = demod._bits_to_bytes(bits)
        score = demod._score_alignment(data)
        if score > best_score:
            best_score = score
            best_offset = offset

print(f"Best offset: {best_offset} (score: {best_score})")

# Now test different approaches at best offset
print(f"\n=== No Trim (baseline) at offset {best_offset} ===")
mm, sm = demod._dft_magnitudes(recorded, best_offset)
analyze_and_demod(mm, sm, "no trim")

for trim in [0.10, 0.15, 0.20, 0.25, 0.30]:
    print(f"\n=== Trim {trim*100:.0f}% each side at offset {best_offset} ===")
    mm, sm = dft_with_trim(recorded, best_offset, trim)
    analyze_and_demod(mm, sm, f"trim {trim}")

for win in ['hann', 'hamming']:
    print(f"\n=== {win.title()} Window at offset {best_offset} ===")
    mm, sm = dft_with_window(recorded, best_offset, win)
    analyze_and_demod(mm, sm, win)

# Also test trim + window combined
print(f"\n=== Trim 20% + Hann Window at offset {best_offset} ===")
# Custom: trim 20% then apply hann to remaining
trim_frac = 0.20
trim_s = int(spb * trim_frac)
usable = spb - 2 * trim_s
num_bits = (len(recorded) - best_offset) // spb
t = np.arange(usable, dtype=np.float64) / sr
win = np.hanning(usable)
mc_ref = np.cos(2 * np.pi * 1200 * t) * win
ms_ref = np.sin(2 * np.pi * 1200 * t) * win
sc_ref = np.cos(2 * np.pi * 2200 * t) * win
ss_ref = np.sin(2 * np.pi * 2200 * t) * win

mm_tw = np.empty(num_bits)
sm_tw = np.empty(num_bits)
for i in range(num_bits):
    start = best_offset + i * spb + trim_s
    end = start + usable
    if end > len(recorded):
        mm_tw = mm_tw[:i]
        sm_tw = sm_tw[:i]
        break
    chunk = recorded[start:end].astype(np.float64)
    mc = np.dot(chunk, mc_ref)
    ms = np.dot(chunk, ms_ref)
    sc = np.dot(chunk, sc_ref)
    ss = np.dot(chunk, ss_ref)
    mm_tw[i] = mc * mc + ms * ms
    sm_tw[i] = sc * sc + ss * ss
analyze_and_demod(mm_tw, sm_tw, "trim+hann")
