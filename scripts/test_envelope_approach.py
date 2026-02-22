#!/usr/bin/env python3
"""Test envelope-based demodulation with various normalization schemes."""
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

# Compute envelopes once
from scipy import signal as sig
mark_env = demod._envelope_detect(recorded, demod.mark_filter)
space_env = demod._envelope_detect(recorded, demod.space_filter)

print(f"\nRaw envelope stats:")
print(f"  Mark:  mean={np.mean(mark_env):.6f}, max={np.max(mark_env):.6f}")
print(f"  Space: mean={np.mean(space_env):.6f}, max={np.max(space_env):.6f}")
print(f"  Space/Mark ratio: {np.mean(space_env)/max(np.mean(mark_env), 1e-10):.2f}x")


def demod_envelope(mark_e, space_e, offset, label):
    """Demodulate using envelope comparison at given offset."""
    bits = []
    num_bits = (len(mark_e) - offset) // spb
    for i in range(num_bits):
        start = offset + i * spb
        end = start + spb
        m = np.mean(mark_e[start:end])
        s = np.mean(space_e[start:end])
        bits.append(1 if m > s else 0)
    data = demod._bits_to_bytes(bits)
    return data


def score_and_report(data, label):
    """Score and report demodulation results."""
    if len(data) == 0:
        print(f"  {label}: no data")
        return -1
    aa = sum(1 for b in data[:20] if b == 0xAA)
    frame = Frame.decode(data)
    score = demod._score_alignment(data)
    status = "FRAME OK!" if frame else ""
    print(f"  {label}: {len(data)} bytes, AA={aa}/16, score={score} {status}")
    if aa > 0 and len(data) >= 4:
        print(f"    First 30 bytes: {data[:30].hex()}")
    if frame:
        print(f"    *** DECODED: type={frame.frame_type.name} seq={frame.sequence} ***")
    return score


def search_best_offset(mark_e, space_e, label):
    """Search for best bit alignment offset."""
    coarse_step = max(1, spb // 4)
    search_start = max(0, base_offset)
    search_end = min(len(mark_e) - spb * 8, base_offset + spb * 8 * 16)

    best_offset = search_start
    best_score = -1

    for offset in range(search_start, search_end, coarse_step):
        data = demod_envelope(mark_e, space_e, offset, "")
        score = demod._score_alignment(data)
        if score > best_score:
            best_score = score
            best_offset = offset
            if score >= 18:
                break

    # Fine search
    if best_score > 0:
        fine_start = max(0, best_offset - coarse_step)
        fine_end = min(len(mark_e) - spb * 8, best_offset + coarse_step)
        for offset in range(fine_start, fine_end, max(1, spb // 16)):
            data = demod_envelope(mark_e, space_e, offset, "")
            score = demod._score_alignment(data)
            if score > best_score:
                best_score = score
                best_offset = offset

    return best_offset, best_score


# Approach 1: Raw envelopes (no normalization)
print("\n=== Approach 1: Raw Envelopes ===")
off1, sc1 = search_best_offset(mark_env, space_env, "raw")
print(f"  Best offset: {off1}, score: {sc1}")
data1 = demod_envelope(mark_env, space_env, off1, "raw")
score_and_report(data1, "Raw")

# Approach 2: RMS normalization
print("\n=== Approach 2: RMS Normalized Envelopes ===")
mark_rms = np.sqrt(np.mean(mark_env**2)) + 1e-10
space_rms = np.sqrt(np.mean(space_env**2)) + 1e-10
mark_norm = mark_env / mark_rms
space_norm = space_env / space_rms
print(f"  Mark RMS: {mark_rms:.6f}, Space RMS: {space_rms:.6f}")
print(f"  Normalization factor: {space_rms/mark_rms:.2f}x")
off2, sc2 = search_best_offset(mark_norm, space_norm, "rms")
print(f"  Best offset: {off2}, score: {sc2}")
data2 = demod_envelope(mark_norm, space_norm, off2, "rms_norm")
score_and_report(data2, "RMS Norm")

# Approach 3: Mean normalization
print("\n=== Approach 3: Mean Normalized Envelopes ===")
mark_mean = np.mean(mark_env) + 1e-10
space_mean = np.mean(space_env) + 1e-10
mark_mnorm = mark_env / mark_mean
space_mnorm = space_env / space_mean
off3, sc3 = search_best_offset(mark_mnorm, space_mnorm, "mean")
print(f"  Best offset: {off3}, score: {sc3}")
data3 = demod_envelope(mark_mnorm, space_mnorm, off3, "mean_norm")
score_and_report(data3, "Mean Norm")

# Approach 4: Peak normalization
print("\n=== Approach 4: Peak Normalized Envelopes ===")
mark_peak = np.max(mark_env) + 1e-10
space_peak = np.max(space_env) + 1e-10
mark_pnorm = mark_env / mark_peak
space_pnorm = space_env / space_peak
off4, sc4 = search_best_offset(mark_pnorm, space_pnorm, "peak")
print(f"  Best offset: {off4}, score: {sc4}")
data4 = demod_envelope(mark_pnorm, space_pnorm, off4, "peak_norm")
score_and_report(data4, "Peak Norm")

# Approach 5: Signal-portion RMS normalization (only normalize where signal exists)
print("\n=== Approach 5: Signal-Portion RMS Normalization ===")
sig_start = max(0, base_offset - spb * 2)
sig_end = min(len(recorded), base_offset + spb * 250)
mark_sig_rms = np.sqrt(np.mean(mark_env[sig_start:sig_end]**2)) + 1e-10
space_sig_rms = np.sqrt(np.mean(space_env[sig_start:sig_end]**2)) + 1e-10
mark_snorm = mark_env / mark_sig_rms
space_snorm = space_env / space_sig_rms
print(f"  Signal portion Mark RMS: {mark_sig_rms:.6f}, Space RMS: {space_sig_rms:.6f}")
print(f"  Normalization factor: {space_sig_rms/mark_sig_rms:.2f}x")
off5, sc5 = search_best_offset(mark_snorm, space_snorm, "sig_rms")
print(f"  Best offset: {off5}, score: {sc5}")
data5 = demod_envelope(mark_snorm, space_snorm, off5, "sig_rms_norm")
score_and_report(data5, "Sig RMS Norm")

# Approach 6: Wider bandwidth filters
print("\n=== Approach 6: Wider Bandwidth (1000 Hz) ===")
nyquist = sr / 2
bw = 1000
for bw in [1000, 1200]:
    # Mark filter: 1200 Hz +/- bw/2
    low = max(0.001, (1200 - bw/2) / nyquist)
    high = min(0.999, (1200 + bw/2) / nyquist)
    b_m, a_m = sig.butter(2, [low, high], btype='band')
    # Space filter: 2200 Hz +/- bw/2
    low = max(0.001, (2200 - bw/2) / nyquist)
    high = min(0.999, (2200 + bw/2) / nyquist)
    b_s, a_s = sig.butter(2, [low, high], btype='band')
    # Envelope
    cutoff = min(0.999, 600 / nyquist)
    b_lp, a_lp = sig.butter(2, cutoff, btype='low')

    filtered_m = sig.lfilter(b_m, a_m, recorded)
    env_m = sig.lfilter(b_lp, a_lp, np.abs(filtered_m))
    filtered_s = sig.lfilter(b_s, a_s, recorded)
    env_s = sig.lfilter(b_lp, a_lp, np.abs(filtered_s))

    # RMS normalize
    m_rms = np.sqrt(np.mean(env_m**2)) + 1e-10
    s_rms = np.sqrt(np.mean(env_s**2)) + 1e-10
    env_mn = env_m / m_rms
    env_sn = env_s / s_rms

    off6, sc6 = search_best_offset(env_mn, env_sn, f"bw{bw}")
    print(f"  BW={bw}: Best offset: {off6}, score: {sc6}")
    data6 = demod_envelope(env_mn, env_sn, off6, f"bw{bw}")
    score_and_report(data6, f"BW={bw}")

# Approach 7: Different filter orders
print("\n=== Approach 7: Higher Filter Order ===")
for order in [3, 4, 6]:
    bw = 800
    low = max(0.001, (1200 - bw/2) / nyquist)
    high = min(0.999, (1200 + bw/2) / nyquist)
    b_m, a_m = sig.butter(order, [low, high], btype='band')
    low = max(0.001, (2200 - bw/2) / nyquist)
    high = min(0.999, (2200 + bw/2) / nyquist)
    b_s, a_s = sig.butter(order, [low, high], btype='band')
    cutoff = min(0.999, 600 / nyquist)
    b_lp, a_lp = sig.butter(2, cutoff, btype='low')

    filtered_m = sig.lfilter(b_m, a_m, recorded)
    env_m = sig.lfilter(b_lp, a_lp, np.abs(filtered_m))
    filtered_s = sig.lfilter(b_s, a_s, recorded)
    env_s = sig.lfilter(b_lp, a_lp, np.abs(filtered_s))

    m_rms = np.sqrt(np.mean(env_m**2)) + 1e-10
    s_rms = np.sqrt(np.mean(env_s**2)) + 1e-10
    env_mn = env_m / m_rms
    env_sn = env_s / s_rms

    off7, sc7 = search_best_offset(env_mn, env_sn, f"order{order}")
    print(f"  Order={order}: Best offset: {off7}, score: {sc7}")
    data7 = demod_envelope(env_mn, env_sn, off7, f"order{order}")
    score_and_report(data7, f"Order={order}")

# Approach 8: Zero-phase (filtfilt) with normalization
print("\n=== Approach 8: Zero-Phase Filters (filtfilt) + Normalization ===")
bw = 800
low = max(0.001, (1200 - bw/2) / nyquist)
high = min(0.999, (1200 + bw/2) / nyquist)
b_m, a_m = sig.butter(2, [low, high], btype='band')
low = max(0.001, (2200 - bw/2) / nyquist)
high = min(0.999, (2200 + bw/2) / nyquist)
b_s, a_s = sig.butter(2, [low, high], btype='band')
cutoff = min(0.999, 600 / nyquist)
b_lp, a_lp = sig.butter(2, cutoff, btype='low')

filtered_m = sig.filtfilt(b_m, a_m, recorded)
env_m = sig.filtfilt(b_lp, a_lp, np.abs(filtered_m))
filtered_s = sig.filtfilt(b_s, a_s, recorded)
env_s = sig.filtfilt(b_lp, a_lp, np.abs(filtered_s))

m_rms = np.sqrt(np.mean(env_m**2)) + 1e-10
s_rms = np.sqrt(np.mean(env_s**2)) + 1e-10
env_mn = env_m / m_rms
env_sn = env_s / s_rms

off8, sc8 = search_best_offset(env_mn, env_sn, "filtfilt")
print(f"  Best offset: {off8}, score: {sc8}")
data8 = demod_envelope(env_mn, env_sn, off8, "filtfilt")
score_and_report(data8, "filtfilt+norm")

# Approach 9: Squared envelope (energy) with normalization
print("\n=== Approach 9: Squared Envelope (Energy Detection) ===")
mark_env_sq = mark_env ** 2
space_env_sq = space_env ** 2
m_sq_rms = np.sqrt(np.mean(mark_env_sq**2)) + 1e-10
s_sq_rms = np.sqrt(np.mean(space_env_sq**2)) + 1e-10
mark_sq_norm = mark_env_sq / m_sq_rms
space_sq_norm = space_env_sq / s_sq_rms
off9, sc9 = search_best_offset(mark_sq_norm, space_sq_norm, "energy")
print(f"  Best offset: {off9}, score: {sc9}")
data9 = demod_envelope(mark_sq_norm, space_sq_norm, off9, "energy")
score_and_report(data9, "Energy Norm")

print("\n=== Summary ===")
approaches = [
    ("Raw", sc1), ("RMS Norm", sc2), ("Mean Norm", sc3), ("Peak Norm", sc4),
    ("Sig RMS", sc5), ("filtfilt+norm", sc8), ("Energy", sc9)
]
for name, score in sorted(approaches, key=lambda x: -x[1]):
    print(f"  {name:20s}: score={score}")
