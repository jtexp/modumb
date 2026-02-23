#!/usr/bin/env python3
"""Diagnostic: isolate VAC demodulation degradation over sustained frame exchange.

HTTPS E2E tests fail at frame seq=8 — the demodulator sees 1200 Hz (mark)
everywhere. This script runs 5 phases to pinpoint the root cause:

  Phase 1: Cable 1 only (proxy TX -> relay RX)
  Phase 2: Cable 2 only (relay TX -> proxy RX)
  Phase 3: Alternating bidirectional (simulates real protocol)
  Phase 4: Concurrent TX/RX (both cables simultaneously)
  Phase 5: Stream reset recovery (Phase 3 + periodic stop/start)

Per-frame metrics: success, preamble/sync score, mark_ratio, confidence.
Failed frames save WAV to --wav-dir for offline analysis.

Usage:
    .venv/Scripts/python.exe C:/Users/John/modumb/scripts/diag_vac_degradation.py
    .venv/Scripts/python.exe C:/Users/John/modumb/scripts/diag_vac_degradation.py --phases 1,2 --frames 10
"""
import sys
import os
import argparse
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))
from vac_lock import vac_lock

import numpy as np
from scipy.io import wavfile

from modumb.modem.modem import Modem
from modumb.modem.afsk import AFSKDemodulator
from modumb.modem.profiles import get_profile
from modumb.datalink.frame import Frame

# Device mapping — two VAC cables (same as diag_modem_exchange.py)
PROXY_OUTPUT = 11   # VAC Cable 1 Line Out (proxy TX)
PROXY_INPUT = 5     # VAC Cable 2 Line In (proxy RX)
RELAY_OUTPUT = 8    # VAC Cable 2 Line Out (relay TX)
RELAY_INPUT = 3     # VAC Cable 1 Line In (relay RX)

# Fixed 64-byte payload for consistent mark/space distribution across frames
FIXED_PAYLOAD = (b'DIAG' + bytes(range(60)))[:64]


def compute_frame_metrics(samples, sample_rate, baud_rate):
    """Compute demodulation quality metrics from raw RX samples.

    Uses a fresh demodulator instance to isolate from any accumulated
    filter state in the modem's demodulator.

    Returns dict with: score, mark_ratio, confidence
    """
    if samples is None or len(samples) == 0:
        return {'score': 0, 'mark_ratio': 1.0, 'confidence': 0}

    demod = AFSKDemodulator(sample_rate=sample_rate, baud_rate=baud_rate)

    # Find signal start and demodulate for score
    offset = demod.find_signal_start(samples)
    data = demod.demodulate(samples)
    score = demod._score_alignment(data)

    # Compute per-bit mark/space DFT magnitudes for ratio analysis
    mark_mags, space_mags = demod._dft_magnitudes(samples, offset)

    if len(mark_mags) == 0:
        return {'score': score, 'mark_ratio': 1.0, 'confidence': 0}

    # Filter to bits with actual signal (above noise floor)
    total = mark_mags + space_mags
    signal_threshold = np.max(total) * 0.05
    signal_mask = total > signal_threshold

    if np.sum(signal_mask) < 8:
        return {'score': score, 'mark_ratio': 1.0, 'confidence': 0}

    signal_mark = mark_mags[signal_mask]
    signal_space = space_mags[signal_mask]
    signal_total = signal_mark + signal_space

    # mark_ratio: mean of mark / (mark + space) per bit
    # Healthy: ~0.50, Degraded: >0.70
    ratios = signal_mark / (signal_total + 1e-10)
    mark_ratio = float(np.mean(ratios))

    # confidence: separation between mark and space clusters
    # Uses 25th/75th percentile to find cluster centers
    sorted_ratios = np.sort(ratios)
    n = len(sorted_ratios)
    space_center = float(np.mean(sorted_ratios[:max(1, n // 4)]))
    mark_center = float(np.mean(sorted_ratios[-max(1, n // 4):]))
    # Perfect separation = 100%, no separation = 0%
    confidence = int(min(100, max(0, (mark_center - space_center) * 200)))

    return {'score': score, 'mark_ratio': mark_ratio, 'confidence': confidence}


def save_wav(samples, sample_rate, path):
    """Save float32 samples to int16 WAV file."""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    wavfile.write(path, sample_rate, (samples * 32767).astype(np.int16))


def send_and_receive(tx_modem, rx_modem, frame, timeout=8.0):
    """Send frame on tx_modem, receive on rx_modem using background RX thread.

    Returns (success, rx_samples) where rx_samples are the raw captured audio.
    """
    frame_bytes = frame.encode()
    result = {'data': None, 'error': None}

    def rx_thread():
        try:
            data = rx_modem.receive(timeout=timeout)
            result['data'] = data
        except Exception as e:
            result['error'] = str(e)

    rx_t = threading.Thread(target=rx_thread)
    rx_t.start()
    time.sleep(0.5)  # Let receiver settle

    tx_modem.send(frame_bytes, blocking=True)
    rx_t.join(timeout=timeout + 5)

    if result['error'] or result['data'] is None:
        return False, rx_modem._last_rx_samples

    # Try to decode the received bytes
    decoded = Frame.decode(result['data'])
    if decoded is None:
        return False, rx_modem._last_rx_samples

    success = (decoded.frame_type == frame.frame_type and
               decoded.sequence == frame.sequence and
               decoded.payload == frame.payload)
    return success, rx_modem._last_rx_samples


def run_phase(phase_num, label, tx_modem, rx_modem, num_frames, baud_rate,
              wav_dir, interval=1.0):
    """Run a single-direction phase: send num_frames from tx to rx.

    Returns list of per-frame result dicts.
    """
    tag = f'phase{phase_num}'
    print(f'\n=== Phase {phase_num}: {label} ===')
    results = []

    for i in range(num_frames):
        frame = Frame.create_data(i, FIXED_PAYLOAD)
        success, rx_samples = send_and_receive(tx_modem, rx_modem, frame)
        metrics = compute_frame_metrics(
            rx_samples, rx_modem.sample_rate, baud_rate)

        status = 'PASS' if success else 'FAIL'
        line = (f'[{tag}] Frame #{i:02d}: {status}  '
                f'score={metrics["score"]:<3d} '
                f'mark_ratio={metrics["mark_ratio"]:.2f}  '
                f'confidence={metrics["confidence"]}%')

        if not success and rx_samples is not None:
            wav_path = os.path.join(wav_dir, f'{tag}_frame{i:02d}.wav')
            save_wav(rx_samples, rx_modem.sample_rate, wav_path)
            line += f'  (saved: {wav_path})'

        print(line, flush=True)
        results.append({
            'frame': i, 'success': success, **metrics
        })

        if i < num_frames - 1:
            time.sleep(interval)

    return results


def run_phase_alternating(phase_num, label, proxy_modem, relay_modem,
                          num_frames, baud_rate, wav_dir, interval=0.5):
    """Phase 3: alternating bidirectional frames."""
    tag = f'phase{phase_num}'
    print(f'\n=== Phase {phase_num}: {label} ===')
    results = []

    for i in range(num_frames):
        frame = Frame.create_data(i, FIXED_PAYLOAD)

        if i % 2 == 0:
            # Even: proxy -> relay (Cable 1)
            direction = 'proxy->relay'
            success, rx_samples = send_and_receive(
                proxy_modem, relay_modem, frame)
            rx_sr = relay_modem.sample_rate
        else:
            # Odd: relay -> proxy (Cable 2)
            direction = 'relay->proxy'
            success, rx_samples = send_and_receive(
                relay_modem, proxy_modem, frame)
            rx_sr = proxy_modem.sample_rate

        metrics = compute_frame_metrics(rx_samples, rx_sr, baud_rate)

        status = 'PASS' if success else 'FAIL'
        line = (f'[{tag}] Frame #{i:02d} ({direction}): {status}  '
                f'score={metrics["score"]:<3d} '
                f'mark_ratio={metrics["mark_ratio"]:.2f}  '
                f'confidence={metrics["confidence"]}%')

        if not success and rx_samples is not None:
            wav_path = os.path.join(wav_dir, f'{tag}_frame{i:02d}.wav')
            save_wav(rx_samples, rx_sr, wav_path)
            line += f'  (saved: {wav_path})'

        print(line, flush=True)
        results.append({
            'frame': i, 'direction': direction, 'success': success, **metrics
        })

        if i < num_frames - 1:
            time.sleep(interval)

    return results


def run_phase_concurrent(phase_num, label, proxy_modem, relay_modem,
                         num_frames, baud_rate, wav_dir, interval=1.0):
    """Phase 4: concurrent TX/RX on both cables simultaneously."""
    tag = f'phase{phase_num}'
    print(f'\n=== Phase {phase_num}: {label} ===')
    results = []

    for i in range(num_frames):
        frame = Frame.create_data(i, FIXED_PAYLOAD)
        frame_bytes = frame.encode()

        # Results from both directions
        r1 = {'success': False, 'samples': None}
        r2 = {'success': False, 'samples': None}

        def cable1_exchange():
            """proxy TX -> relay RX via Cable 1"""
            try:
                data = relay_modem.receive(timeout=8.0)
                r1['samples'] = relay_modem._last_rx_samples
                if data:
                    decoded = Frame.decode(data)
                    r1['success'] = (decoded is not None and
                                     decoded.payload == frame.payload)
            except Exception:
                pass

        def cable2_exchange():
            """relay TX -> proxy RX via Cable 2"""
            try:
                data = proxy_modem.receive(timeout=8.0)
                r2['samples'] = proxy_modem._last_rx_samples
                if data:
                    decoded = Frame.decode(data)
                    r2['success'] = (decoded is not None and
                                     decoded.payload == frame.payload)
            except Exception:
                pass

        # Start RX threads
        t1 = threading.Thread(target=cable1_exchange)
        t2 = threading.Thread(target=cable2_exchange)
        t1.start()
        t2.start()
        time.sleep(0.5)

        # TX simultaneously on both cables
        tx1 = threading.Thread(
            target=lambda: proxy_modem.send(frame_bytes, blocking=True))
        tx2 = threading.Thread(
            target=lambda: relay_modem.send(frame_bytes, blocking=True))
        tx1.start()
        tx2.start()
        tx1.join(timeout=10)
        tx2.join(timeout=10)
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Report Cable 1 result
        m1 = compute_frame_metrics(
            r1['samples'], relay_modem.sample_rate, baud_rate)
        s1 = 'PASS' if r1['success'] else 'FAIL'
        line1 = (f'[{tag}] Frame #{i:02d} (cable1): {s1}  '
                 f'score={m1["score"]:<3d} '
                 f'mark_ratio={m1["mark_ratio"]:.2f}  '
                 f'confidence={m1["confidence"]}%')
        if not r1['success'] and r1['samples'] is not None:
            wav_path = os.path.join(wav_dir, f'{tag}_frame{i:02d}_cable1.wav')
            save_wav(r1['samples'], relay_modem.sample_rate, wav_path)
            line1 += f'  (saved: {wav_path})'
        print(line1, flush=True)

        # Report Cable 2 result
        m2 = compute_frame_metrics(
            r2['samples'], proxy_modem.sample_rate, baud_rate)
        s2 = 'PASS' if r2['success'] else 'FAIL'
        line2 = (f'[{tag}] Frame #{i:02d} (cable2): {s2}  '
                 f'score={m2["score"]:<3d} '
                 f'mark_ratio={m2["mark_ratio"]:.2f}  '
                 f'confidence={m2["confidence"]}%')
        if not r2['success'] and r2['samples'] is not None:
            wav_path = os.path.join(wav_dir, f'{tag}_frame{i:02d}_cable2.wav')
            save_wav(r2['samples'], proxy_modem.sample_rate, wav_path)
            line2 += f'  (saved: {wav_path})'
        print(line2, flush=True)

        results.append({
            'frame': i,
            'cable1_success': r1['success'], 'cable1_metrics': m1,
            'cable2_success': r2['success'], 'cable2_metrics': m2,
        })

        if i < num_frames - 1:
            time.sleep(interval)

    return results


def run_phase_with_reset(phase_num, label, proxy_modem, relay_modem,
                         num_frames, baud_rate, wav_dir, reset_every=5,
                         interval=0.5):
    """Phase 5: alternating bidirectional with periodic stream reset."""
    tag = f'phase{phase_num}'
    print(f'\n=== Phase {phase_num}: {label} ===')
    results = []

    for i in range(num_frames):
        # Reset streams every N frames
        if i > 0 and i % reset_every == 0:
            print(f'[{tag}] --- Resetting streams at frame #{i} ---',
                  flush=True)
            proxy_modem.stop()
            relay_modem.stop()
            time.sleep(0.5)
            proxy_modem.start()
            relay_modem.start()
            time.sleep(1.0)  # Let streams settle

        frame = Frame.create_data(i, FIXED_PAYLOAD)

        if i % 2 == 0:
            direction = 'proxy->relay'
            success, rx_samples = send_and_receive(
                proxy_modem, relay_modem, frame)
            rx_sr = relay_modem.sample_rate
        else:
            direction = 'relay->proxy'
            success, rx_samples = send_and_receive(
                relay_modem, proxy_modem, frame)
            rx_sr = proxy_modem.sample_rate

        metrics = compute_frame_metrics(rx_samples, rx_sr, baud_rate)

        status = 'PASS' if success else 'FAIL'
        line = (f'[{tag}] Frame #{i:02d} ({direction}): {status}  '
                f'score={metrics["score"]:<3d} '
                f'mark_ratio={metrics["mark_ratio"]:.2f}  '
                f'confidence={metrics["confidence"]}%')

        if not success and rx_samples is not None:
            wav_path = os.path.join(wav_dir, f'{tag}_frame{i:02d}.wav')
            save_wav(rx_samples, rx_sr, wav_path)
            line += f'  (saved: {wav_path})'

        print(line, flush=True)
        results.append({
            'frame': i, 'direction': direction, 'success': success, **metrics
        })

        if i < num_frames - 1:
            time.sleep(interval)

    return results


def summarize_phase(phase_num, label, results):
    """Summarize a phase's results. Returns (passed, total, first_fail)."""
    if not results:
        return 0, 0, None

    # Phase 4 has two cables per frame
    if 'cable1_success' in results[0]:
        passed = sum(1 for r in results
                     if r['cable1_success'] and r['cable2_success'])
        total = len(results)
        first_fail = None
        for r in results:
            if not r['cable1_success'] or not r['cable2_success']:
                first_fail = r['frame']
                break
    else:
        passed = sum(1 for r in results if r['success'])
        total = len(results)
        first_fail = None
        for r in results:
            if not r['success']:
                first_fail = r['frame']
                break

    degradation = ''
    if first_fail is not None:
        degradation = f', degradation starts at frame {first_fail}'
    else:
        degradation = ', no degradation'

    print(f'Phase {phase_num} ({label}): '
          f'{passed}/{total} passed{degradation}')

    return passed, total, first_fail


def diagnose(phase_results):
    """Compare phase results and print diagnosis."""
    print('\n=== Diagnosis ===')

    # Extract pass rates and first-fail frame for each phase
    info = {}
    for phase_num, (label, results) in phase_results.items():
        passed, total, first_fail = summarize_phase(
            phase_num, label, results)
        info[phase_num] = {
            'passed': passed, 'total': total, 'first_fail': first_fail,
            'pass_rate': passed / total if total > 0 else 0,
            'label': label,
        }

    def failed(p):
        return p in info and info[p]['pass_rate'] < 0.9

    def passed(p):
        return p in info and info[p]['pass_rate'] >= 0.9

    # Diagnostic logic
    if 1 in info and 2 in info:
        if failed(2) and passed(1):
            print('DIAGNOSIS: Cable 2 degrades independently '
                  '(driver/hardware issue on VAC Cable 2).')
        elif failed(1) and passed(2):
            print('DIAGNOSIS: Cable 1 degrades independently '
                  '(driver/hardware issue on VAC Cable 1).')
        elif failed(1) and failed(2):
            print('DIAGNOSIS: Both cables degrade independently '
                  '(systemic driver/hardware issue).')

    if 3 in info and 1 in info and 2 in info:
        if failed(3) and passed(1) and passed(2):
            print('DIAGNOSIS: Cross-cable alternation causes degradation '
                  '(interaction between Cable 1 and Cable 2 streams).')

    if 4 in info and 3 in info:
        if failed(4) and passed(3):
            print('DIAGNOSIS: GIL contention during concurrent audio I/O '
                  'causes degradation.')

    if 5 in info and 3 in info:
        if passed(5) and failed(3):
            print('DIAGNOSIS: Periodic stream reset prevents degradation. '
                  'Fix: stop/start PortAudio streams periodically.')
        elif failed(5) and failed(3):
            print('DIAGNOSIS: Stream reset does NOT prevent degradation. '
                  'Issue is not accumulated stream state.')

    # If nothing triggered, report what we see
    all_passed = all(passed(p) for p in info)
    all_failed = all(failed(p) for p in info)

    if all_passed:
        print('DIAGNOSIS: No degradation detected in any phase. '
              'Issue may require more frames or different timing.')
    elif all_failed and len(info) >= 3:
        print('DIAGNOSIS: All phases degrade. Issue is fundamental to '
              'VAC driver or PortAudio interaction, not cable-specific '
              'or timing-specific.')


def main():
    parser = argparse.ArgumentParser(
        description='VAC degradation diagnostic (5-phase)')
    parser.add_argument(
        '--phases', default='1,2,3,4,5',
        help='Comma-separated phase numbers to run (default: 1,2,3,4,5)')
    parser.add_argument(
        '--frames', type=int, default=20,
        help='Number of frames per phase (default: 20)')
    parser.add_argument(
        '--baud-rate', type=int, default=1200,
        help='Baud rate (default: 1200)')
    parser.add_argument(
        '--duplex', choices=['half', 'full'], default='full',
        help='Duplex mode (default: full)')
    parser.add_argument(
        '--wav-dir', default='wav',
        help='Directory for WAV dumps of failed frames (default: wav)')
    args = parser.parse_args()

    phases = [int(p) for p in args.phases.split(',')]
    full_duplex = args.duplex == 'full'

    print('=== VAC Degradation Diagnostic ===')
    print(f'Phases: {phases}  Frames: {args.frames}  '
          f'Baud: {args.baud_rate}  Duplex: {args.duplex}')

    with vac_lock():
        profile = get_profile('cable')
        print(f'Profile: {profile.name} (tx_volume={profile.tx_volume})')

        # Create modems
        proxy_modem = Modem(
            input_device=PROXY_INPUT,
            output_device=PROXY_OUTPUT,
            profile=profile,
            baud_rate=args.baud_rate,
            full_duplex=full_duplex,
        )
        relay_modem = Modem(
            input_device=RELAY_INPUT,
            output_device=RELAY_OUTPUT,
            profile=profile,
            baud_rate=args.baud_rate,
            full_duplex=full_duplex,
        )

        print(f'Proxy modem: rate={proxy_modem.sample_rate}Hz')
        print(f'Relay modem: rate={relay_modem.sample_rate}Hz')

        proxy_modem.start()
        relay_modem.start()
        time.sleep(1)  # Let streams settle

        phase_results = {}

        try:
            if 1 in phases:
                label = 'Cable 1 only (proxy TX -> relay RX)'
                results = run_phase(
                    1, label, proxy_modem, relay_modem,
                    args.frames, args.baud_rate, args.wav_dir)
                phase_results[1] = (label, results)

            if 2 in phases:
                label = 'Cable 2 only (relay TX -> proxy RX)'
                results = run_phase(
                    2, label, relay_modem, proxy_modem,
                    args.frames, args.baud_rate, args.wav_dir)
                phase_results[2] = (label, results)

            if 3 in phases:
                label = 'Alternating bidirectional'
                results = run_phase_alternating(
                    3, label, proxy_modem, relay_modem,
                    args.frames, args.baud_rate, args.wav_dir)
                phase_results[3] = (label, results)

            if 4 in phases:
                label = 'Concurrent TX/RX (both cables)'
                results = run_phase_concurrent(
                    4, label, proxy_modem, relay_modem,
                    args.frames, args.baud_rate, args.wav_dir)
                phase_results[4] = (label, results)

            if 5 in phases:
                label = 'Stream reset recovery'
                results = run_phase_with_reset(
                    5, label, proxy_modem, relay_modem,
                    args.frames, args.baud_rate, args.wav_dir)
                phase_results[5] = (label, results)

        finally:
            proxy_modem.stop()
            relay_modem.stop()

        # Summary
        print('\n=== Summary ===')
        diagnose(phase_results)

    return 0


if __name__ == '__main__':
    sys.exit(main())
