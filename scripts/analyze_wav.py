"""Analyze a WAV dump from a failed modem frame decode.

Usage: python analyze_wav.py <wav_file>

Produces:
  - Waveform overview (amplitude vs time)
  - Spectrogram (frequency content over time)
  - Per-bit mark/space DFT magnitudes
  - Bit alignment analysis
  - Comparison of demodulation strategies
"""
import sys
import os
import numpy as np
from scipy.io import wavfile

# Add project source to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from modumb.modem.afsk import AFSKDemodulator, AFSKModulator, SAMPLE_RATE
from modumb.datalink.frame import Frame, PREAMBLE, SYNC


def analyze_wav(path):
    sr, data = wavfile.read(path)
    # Convert int16 to float32
    if data.dtype == np.int16:
        samples = data.astype(np.float32) / 32767.0
    else:
        samples = data.astype(np.float32)

    print(f"WAV: {path}")
    print(f"  Sample rate: {sr} Hz")
    print(f"  Samples: {len(samples)} ({len(samples)/sr*1000:.1f} ms)")
    print(f"  Peak amplitude: {np.max(np.abs(samples)):.4f}")
    print(f"  RMS: {np.sqrt(np.mean(samples**2)):.4f}")
    print()

    baud = 1200
    spb = sr // baud
    demod = AFSKDemodulator(sample_rate=sr, baud_rate=baud)

    # ---- Signal detection ----
    abs_samples = np.abs(samples)
    max_amp = float(np.max(abs_samples))
    threshold = max_amp * 0.1
    above = np.where(abs_samples > threshold)[0]
    if len(above) == 0:
        print("ERROR: No signal detected!")
        return

    signal_start = above[0]
    signal_end = above[-1]
    print(f"Signal region: sample {signal_start} - {signal_end} "
          f"({signal_start/sr*1000:.1f}ms - {signal_end/sr*1000:.1f}ms)")
    print(f"  Signal duration: {(signal_end - signal_start)/sr*1000:.1f} ms")
    print(f"  Lead silence: {signal_start/sr*1000:.1f} ms")
    print(f"  Trail silence: {(len(samples)-signal_end)/sr*1000:.1f} ms")
    print()

    # ---- Trim with same logic as modem.py ----
    margin = spb * 16  # same as modem.py now
    trim_start = max(0, int(above[0]) - margin)
    trimmed = samples[trim_start:]
    print(f"After trim (margin={margin} samples = {margin/sr*1000:.1f}ms): "
          f"{len(trimmed)} samples from offset {trim_start}")
    print()

    # ---- Demodulate with all strategies ----
    mark_env, space_env = demod._compute_normalized_envelopes(trimmed)
    base_offset = demod.find_signal_start(trimmed)
    print(f"Signal start (find_signal_start): sample {base_offset} "
          f"({base_offset/sr*1000:.1f}ms)")

    # Coarse search
    coarse_step = max(1, spb // 4)
    search_start = max(0, base_offset)
    search_end = min(len(trimmed) - spb * 8, base_offset + spb * 8 * 16)

    best_offset = search_start
    best_score = -1
    for offset in range(search_start, search_end, coarse_step):
        data = demod._demodulate_envelope(mark_env, space_env, offset)
        score = demod._score_alignment(data)
        if score > best_score:
            best_score = score
            best_offset = offset
            if score >= 18:
                break

    # Fine search
    if best_score > 0:
        fine_start = max(0, best_offset - coarse_step)
        fine_end = min(len(trimmed) - spb * 8, best_offset + coarse_step)
        fine_step = max(1, spb // 16)
        for offset in range(fine_start, fine_end, fine_step):
            data = demod._demodulate_envelope(mark_env, space_env, offset)
            score = demod._score_alignment(data)
            if score > best_score:
                best_score = score
                best_offset = offset

    print(f"Best alignment: offset={best_offset} ({best_offset/sr*1000:.1f}ms) score={best_score}")
    print()

    # ---- All three strategies ----
    strats = {}
    strats['envelope_cr'] = demod._demodulate_envelope_recovered(
        mark_env, space_env, best_offset)
    strats['dft_cr'] = demod._demodulate_dft_recovered(
        trimmed, mark_env, space_env, best_offset)
    strats['dft_simple'] = demod._demodulate_dft(trimmed, best_offset)

    for name, result in strats.items():
        score = demod._score_alignment(result)
        frame = Frame.decode(result)
        print(f"Strategy: {name} ({len(result)} bytes, score={score})")
        print(f"  Hex: {result[:60].hex()}")
        if frame:
            print(f"  FRAME DECODED: type={frame.frame_type.name} seq={frame.sequence} "
                  f"payload={len(frame.payload)}B")
        else:
            # Check for preamble/sync
            preamble_count = sum(1 for b in result[:20] if b == 0xAA)
            sync_pos = -1
            for i in range(len(result) - 1):
                if result[i] == 0x7E and result[i+1] == 0x7E:
                    sync_pos = i
                    break
            print(f"  Preamble bytes (first 20): {preamble_count}/20")
            print(f"  SYNC position: {sync_pos}")
            if sync_pos >= 0 and sync_pos + 7 < len(result):
                after_sync = result[sync_pos+2:sync_pos+7]
                if len(after_sync) >= 5:
                    ftype = after_sync[0]
                    seq = int.from_bytes(after_sync[1:3], 'little')
                    length = int.from_bytes(after_sync[3:5], 'little')
                    print(f"  Header: type={ftype} seq={seq} len={length}")
        print()

    # ---- Per-bit DFT analysis around failure point ----
    print("=" * 60)
    print("Per-bit DFT magnitude analysis (mark vs space):")
    print("=" * 60)
    mark_mags, space_mags = demod._dft_magnitudes(trimmed, best_offset)

    num_bits = len(mark_mags)
    print(f"Total bits: {num_bits} ({num_bits // 8} bytes)")
    print()

    # Show per-byte breakdown
    total = mark_mags + space_mags
    mark_ratio = mark_mags / (total + 1e-10)

    # Expected frame: 16 preamble (0xAA) + 2 sync (0x7E) + 1 type + 2 seq + 2 len + 64 payload + 2 CRC
    byte_labels = ['PRE'] * 16 + ['SYN'] * 2 + ['TYP'] + ['SEQ'] * 2 + ['LEN'] * 2 + ['PAY'] * 64 + ['CRC'] * 2
    for byte_idx in range(min(num_bits // 8, len(byte_labels))):
        bit_start = byte_idx * 8
        byte_val = 0
        bits_str = ""
        for j in range(8):
            bi = bit_start + j
            if bi >= num_bits:
                break
            bit = 1 if mark_ratio[bi] > 0.5 else 0
            byte_val |= bit << j
            ratio = mark_ratio[bi]
            confidence = abs(ratio - 0.5) * 200  # 0-100%
            bits_str += f"{bit}"
            if confidence < 20:
                bits_str = bits_str[:-1] + f"[{bit}?{confidence:.0f}%]"

        label = byte_labels[byte_idx] if byte_idx < len(byte_labels) else '???'
        mag_avg = np.mean(total[bit_start:bit_start+8])
        print(f"  Byte {byte_idx:3d} [{label}]: 0x{byte_val:02X} "
              f"bits={bits_str} mag={mag_avg:.1f}")

        # Flag bytes with low-confidence bits
        byte_confidences = []
        for j in range(8):
            bi = bit_start + j
            if bi < num_bits:
                byte_confidences.append(abs(mark_ratio[bi] - 0.5) * 200)
        min_conf = min(byte_confidences) if byte_confidences else 100
        if min_conf < 30:
            print(f"         *** LOW CONFIDENCE (min={min_conf:.0f}%) ***")

    # ---- Energy profile over time ----
    print()
    print("=" * 60)
    print("Signal energy per 10-byte chunk:")
    print("=" * 60)
    chunk_bits = 80  # 10 bytes
    for i in range(0, num_bits - chunk_bits + 1, chunk_bits):
        chunk_energy = np.mean(total[i:i+chunk_bits])
        chunk_mark = np.mean(mark_mags[i:i+chunk_bits])
        chunk_space = np.mean(space_mags[i:i+chunk_bits])
        byte_start = i // 8
        bar = '#' * int(chunk_energy / np.max(total) * 40)
        print(f"  Bytes {byte_start:3d}-{byte_start+9:3d}: "
              f"energy={chunk_energy:10.1f} "
              f"mark={chunk_mark:10.1f} space={chunk_space:10.1f} {bar}")

    # ---- Check for discontinuities ----
    print()
    print("=" * 60)
    print("Sample-level discontinuity check:")
    print("=" * 60)
    # Check for zero-crossings in the raw waveform at unexpected rates
    # and for any sample gaps (zeros in signal region)
    sig = samples[signal_start:signal_end]
    zero_runs = []
    run_start = None
    for i, s in enumerate(sig):
        if abs(s) < 0.001:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None:
                run_len = i - run_start
                if run_len > 4:  # More than 4 consecutive near-zero samples
                    zero_runs.append((signal_start + run_start, run_len))
                run_start = None

    if zero_runs:
        print(f"  Found {len(zero_runs)} zero-runs (>4 samples) in signal region:")
        for pos, length in zero_runs[:10]:
            print(f"    Sample {pos} ({pos/sr*1000:.2f}ms): {length} samples "
                  f"({length/sr*1000:.2f}ms)")
    else:
        print("  No zero-runs detected in signal region (clean waveform)")

    # Check instantaneous frequency
    print()
    analytic = np.abs(np.diff(sig))
    large_jumps = np.where(analytic > max_amp * 0.5)[0]
    jump_rate = len(large_jumps) / len(sig) * sr
    expected_rate = 2 * max(1200, 2200)  # Upper bound: 2x max freq
    print(f"  Large amplitude jumps: {len(large_jumps)} "
          f"({jump_rate:.0f}/sec, expected ~{expected_rate}/sec for AFSK)")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <wav_file>")
        sys.exit(1)
    analyze_wav(sys.argv[1])
