#!/usr/bin/env python3
"""Diagnostic: protocol-level VAC degradation tests.

Follow-up to diag_vac_degradation.py which proved raw modem frames don't degrade.
These tests reproduce progressively more realistic protocol conditions to find
what triggers the HTTPS seq=8 failure.

Tests (run in order):
  T1: Timing sweep — same direction, decreasing inter-frame gaps
  T2: ARQ simulation — DATA/ACK bidirectional exchange
  T3: TLS payload patterns — binary-heavy payloads vs test payload
  T4: Session handshake warmup — SYN/SYN-ACK/ACK before DATA
  T5: Noise probe instrumentation — log noise_rms and queue depth per frame
  T6: RX queue accumulation — monitor queue depth during sustained exchange
  T7: Large plain HTTP — 15+ frames unidirectional to exceed frame-8 threshold

Usage:
    .venv/Scripts/python.exe C:/Users/John/modumb/scripts/diag_vac_degradation2.py
    .venv/Scripts/python.exe C:/Users/John/modumb/scripts/diag_vac_degradation2.py --tests 1,2 --frames 20
"""
import sys
import os
import argparse
import time
import threading
import struct
import io

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))
from vac_lock import vac_lock

import numpy as np
from scipy.io import wavfile

from modumb.modem.modem import Modem
from modumb.modem.afsk import AFSKDemodulator
from modumb.modem.profiles import get_profile
from modumb.datalink.frame import Frame, FrameType

# Device mapping
PROXY_OUTPUT = 11
PROXY_INPUT = 5
RELAY_OUTPUT = 8
RELAY_INPUT = 3

# Fixed 64-byte payload (same as diag_vac_degradation.py)
FIXED_PAYLOAD = (b'DIAG' + bytes(range(60)))[:64]


def compute_metrics(samples, sample_rate, baud_rate):
    """Compute demod quality metrics from raw RX samples (fresh demodulator)."""
    if samples is None or len(samples) == 0:
        return {'score': 0, 'mark_ratio': 1.0, 'confidence': 0}

    demod = AFSKDemodulator(sample_rate=sample_rate, baud_rate=baud_rate)
    offset = demod.find_signal_start(samples)
    data = demod.demodulate(samples)
    score = demod._score_alignment(data)

    mark_mags, space_mags = demod._dft_magnitudes(samples, offset)
    if len(mark_mags) == 0:
        return {'score': score, 'mark_ratio': 1.0, 'confidence': 0}

    total = mark_mags + space_mags
    signal_mask = total > np.max(total) * 0.05
    if np.sum(signal_mask) < 8:
        return {'score': score, 'mark_ratio': 1.0, 'confidence': 0}

    ratios = mark_mags[signal_mask] / (total[signal_mask] + 1e-10)
    mark_ratio = float(np.mean(ratios))
    sorted_r = np.sort(ratios)
    n = len(sorted_r)
    space_c = float(np.mean(sorted_r[:max(1, n // 4)]))
    mark_c = float(np.mean(sorted_r[-max(1, n // 4):]))
    confidence = int(min(100, max(0, (mark_c - space_c) * 200)))

    return {'score': score, 'mark_ratio': mark_ratio, 'confidence': confidence}


def save_wav(samples, sample_rate, path):
    """Save float32 samples to int16 WAV."""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    wavfile.write(path, sample_rate, (samples * 32767).astype(np.int16))


def send_receive(tx_modem, rx_modem, frame_bytes, timeout=8.0):
    """TX on one modem, RX on another via background thread.
    Returns (decoded_frame_or_None, rx_samples).
    """
    result = {'data': None}

    def rx_thread():
        try:
            result['data'] = rx_modem.receive(timeout=timeout)
        except Exception:
            pass

    t = threading.Thread(target=rx_thread)
    t.start()
    time.sleep(0.3)
    tx_modem.send(frame_bytes, blocking=True)
    t.join(timeout=timeout + 5)

    decoded = None
    if result['data']:
        decoded = Frame.decode(result['data'])
    return decoded, rx_modem._last_rx_samples


def fmt_result(tag, i, success, metrics, extra='', wav_path=None):
    """Format a single frame result line."""
    status = 'PASS' if success else 'FAIL'
    line = (f'[{tag}] Frame #{i:02d}: {status}  '
            f'score={metrics["score"]:<3d} '
            f'mark_ratio={metrics["mark_ratio"]:.2f}  '
            f'confidence={metrics["confidence"]}%')
    if extra:
        line += f'  {extra}'
    if wav_path:
        line += f'  (saved: {wav_path})'
    print(line, flush=True)
    return {'frame': i, 'success': success, **metrics}


# ===== T1: Timing sweep =====

def test_timing_sweep(proxy_modem, relay_modem, num_frames, baud_rate, wav_dir):
    """Send frames with progressively tighter inter-frame gaps."""
    tag = 'T1-timing'
    delays = [1.0, 0.5, 0.25, 0.1, 0.05]
    print(f'\n=== T1: Timing sweep (delays: {delays}s) ===')
    results = []

    for delay in delays:
        print(f'\n  --- delay={delay}s ---')
        for i in range(num_frames):
            frame = Frame.create_data(i, FIXED_PAYLOAD)
            decoded, rx_samples = send_receive(
                proxy_modem, relay_modem, frame.encode())
            success = (decoded is not None and decoded.payload == frame.payload)
            m = compute_metrics(rx_samples, relay_modem.sample_rate, baud_rate)

            wp = None
            if not success and rx_samples is not None:
                wp = os.path.join(wav_dir, f'{tag}_d{delay}_f{i:02d}.wav')
                save_wav(rx_samples, relay_modem.sample_rate, wp)

            r = fmt_result(tag, i, success, m,
                           extra=f'delay={delay}s', wav_path=wp)
            r['delay'] = delay
            results.append(r)

            if i < num_frames - 1:
                time.sleep(delay)

    return results


# ===== T2: ARQ simulation =====

def test_arq_simulation(proxy_modem, relay_modem, num_frames, baud_rate, wav_dir):
    """Simulate bidirectional DATA/ACK exchange like real ARQ."""
    tag = 'T2-arq'
    print(f'\n=== T2: ARQ simulation ({num_frames} round trips) ===')
    results = []

    for i in range(num_frames):
        # Proxy sends DATA
        data_frame = Frame.create_data(i, FIXED_PAYLOAD)
        decoded, rx_samples = send_receive(
            proxy_modem, relay_modem, data_frame.encode())
        data_ok = (decoded is not None and decoded.payload == data_frame.payload)
        m_data = compute_metrics(rx_samples, relay_modem.sample_rate, baud_rate)

        wp = None
        if not data_ok and rx_samples is not None:
            wp = os.path.join(wav_dir, f'{tag}_data_{i:02d}.wav')
            save_wav(rx_samples, relay_modem.sample_rate, wp)

        fmt_result(tag, i, data_ok, m_data, extra='DATA proxy->relay', wav_path=wp)

        # Relay sends ACK back (like real ARQ)
        ack_frame = Frame.create_ack(i)
        ack_decoded, ack_samples = send_receive(
            relay_modem, proxy_modem, ack_frame.encode())
        ack_ok = (ack_decoded is not None and
                  ack_decoded.frame_type == FrameType.ACK)
        m_ack = compute_metrics(ack_samples, proxy_modem.sample_rate, baud_rate)

        wp2 = None
        if not ack_ok and ack_samples is not None:
            wp2 = os.path.join(wav_dir, f'{tag}_ack_{i:02d}.wav')
            save_wav(ack_samples, proxy_modem.sample_rate, wp2)

        fmt_result(tag, i, ack_ok, m_ack, extra='ACK relay->proxy', wav_path=wp2)

        results.append({
            'frame': i, 'data_ok': data_ok, 'ack_ok': ack_ok,
            'data_metrics': m_data, 'ack_metrics': m_ack,
        })

        # Real ARQ pacing: FULL_DUPLEX_GUARD (20ms) + FULL_DUPLEX_ACK_GUARD (150ms)
        time.sleep(0.02)

    return results


# ===== T3: TLS payload patterns =====

def test_tls_payloads(proxy_modem, relay_modem, num_frames, baud_rate, wav_dir):
    """Test with different payload byte distributions."""
    tag = 'T3-payload'
    print(f'\n=== T3: TLS payload patterns ===')

    # Different payload types to test mark/space bias
    payloads = {
        'fixed': FIXED_PAYLOAD,
        'all_zero': b'\x00' * 64,       # All mark bits (worst case)
        'all_ff': b'\xff' * 64,          # Also all mark (0xFF = 11111111)
        'all_55': b'\x55' * 64,          # Alternating 0/1 (balanced)
        'tls_hello': bytes([             # Simulated TLS ClientHello header
            0x16, 0x03, 0x01, 0x00, 0x3c, 0x01, 0x00, 0x00,
            0x38, 0x03, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x20, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x13, 0x01,
        ]),
        'random': os.urandom(64),        # Random bytes
    }

    results = {}
    for name, payload in payloads.items():
        # Count mark bits to characterize payload
        mark_bits = sum(bin(b).count('1') for b in payload)
        total_bits = len(payload) * 8
        mark_pct = mark_bits / total_bits * 100

        print(f'\n  --- payload={name} (mark_bits={mark_pct:.0f}%) ---')
        payload_results = []

        for i in range(min(num_frames, 10)):  # 10 frames per payload type
            frame = Frame.create_data(i, payload)
            decoded, rx_samples = send_receive(
                proxy_modem, relay_modem, frame.encode())
            success = (decoded is not None and decoded.payload == payload)
            m = compute_metrics(rx_samples, relay_modem.sample_rate, baud_rate)

            wp = None
            if not success and rx_samples is not None:
                wp = os.path.join(wav_dir, f'{tag}_{name}_{i:02d}.wav')
                save_wav(rx_samples, relay_modem.sample_rate, wp)

            r = fmt_result(tag, i, success, m,
                           extra=f'{name} mark={mark_pct:.0f}%', wav_path=wp)
            payload_results.append(r)
            time.sleep(0.5)

        results[name] = payload_results

    return results


# ===== T4: Session handshake warmup =====

def test_handshake_warmup(proxy_modem, relay_modem, num_frames, baud_rate, wav_dir):
    """SYN/SYN-ACK/ACK handshake then DATA frames."""
    tag = 'T4-handshake'
    print(f'\n=== T4: Session handshake warmup ===')
    results = []

    # Phase A: SYN from proxy
    print('  Sending SYN...')
    syn = Frame.create_syn()
    decoded, rx_samples = send_receive(
        proxy_modem, relay_modem, syn.encode())
    syn_ok = decoded is not None and decoded.frame_type == FrameType.SYN
    m = compute_metrics(rx_samples, relay_modem.sample_rate, baud_rate)
    fmt_result(tag, 0, syn_ok, m, extra='SYN proxy->relay')
    time.sleep(0.3)

    # Phase B: SYN-ACK from relay
    print('  Sending SYN-ACK...')
    syn_ack = Frame.create_syn_ack()
    decoded, rx_samples = send_receive(
        relay_modem, proxy_modem, syn_ack.encode())
    synack_ok = decoded is not None and decoded.frame_type == FrameType.SYN_ACK
    m = compute_metrics(rx_samples, proxy_modem.sample_rate, baud_rate)
    fmt_result(tag, 1, synack_ok, m, extra='SYN-ACK relay->proxy')
    time.sleep(0.3)

    # Phase C: ACK from proxy
    print('  Sending ACK...')
    ack = Frame.create_ack(0)
    decoded, rx_samples = send_receive(
        proxy_modem, relay_modem, ack.encode())
    ack_ok = decoded is not None and decoded.frame_type == FrameType.ACK
    m = compute_metrics(rx_samples, relay_modem.sample_rate, baud_rate)
    fmt_result(tag, 2, ack_ok, m, extra='ACK proxy->relay')

    # Post-handshake delay (same as real protocol)
    time.sleep(0.5)

    # Phase D: DATA frames with ARQ pattern
    print(f'  Sending {num_frames} DATA+ACK round trips...')
    for i in range(num_frames):
        data_frame = Frame.create_data(i, FIXED_PAYLOAD)
        decoded, rx_samples = send_receive(
            proxy_modem, relay_modem, data_frame.encode())
        data_ok = (decoded is not None and decoded.payload == data_frame.payload)
        m = compute_metrics(rx_samples, relay_modem.sample_rate, baud_rate)

        wp = None
        if not data_ok and rx_samples is not None:
            wp = os.path.join(wav_dir, f'{tag}_data_{i:02d}.wav')
            save_wav(rx_samples, relay_modem.sample_rate, wp)
        fmt_result(tag, i + 3, data_ok, m, extra=f'DATA#{i}', wav_path=wp)

        # ACK back
        ack_frame = Frame.create_ack(i)
        decoded, rx_samples = send_receive(
            relay_modem, proxy_modem, ack_frame.encode())
        ack_ok = decoded is not None and decoded.frame_type == FrameType.ACK
        m_ack = compute_metrics(rx_samples, proxy_modem.sample_rate, baud_rate)
        fmt_result(tag, i + 3, ack_ok, m_ack, extra=f'ACK#{i}')

        results.append({
            'frame': i, 'data_ok': data_ok, 'ack_ok': ack_ok,
            'data_metrics': m, 'ack_metrics': m_ack,
        })
        time.sleep(0.02)

    return results


# ===== T5: Noise probe instrumentation =====

def test_noise_probe(proxy_modem, relay_modem, num_frames, baud_rate, wav_dir):
    """ARQ exchange with noise_rms and queue depth logging per frame."""
    tag = 'T5-noise'
    print(f'\n=== T5: Noise probe instrumentation ({num_frames} round trips) ===')
    results = []

    for i in range(num_frames):
        # Log queue depth before DATA receive
        relay_qd = relay_modem.audio._rx_queue.qsize()

        # Proxy sends DATA
        data_frame = Frame.create_data(i, FIXED_PAYLOAD)
        decoded, rx_samples = send_receive(
            proxy_modem, relay_modem, data_frame.encode())
        data_ok = (decoded is not None and decoded.payload == data_frame.payload)
        m = compute_metrics(rx_samples, relay_modem.sample_rate, baud_rate)

        wp = None
        if not data_ok and rx_samples is not None:
            wp = os.path.join(wav_dir, f'{tag}_data_{i:02d}.wav')
            save_wav(rx_samples, relay_modem.sample_rate, wp)

        fmt_result(tag, i, data_ok, m,
                   extra=f'DATA relay_qd={relay_qd}', wav_path=wp)

        # Log queue depth before ACK receive
        proxy_qd = proxy_modem.audio._rx_queue.qsize()

        # Relay sends ACK
        ack_frame = Frame.create_ack(i)
        ack_decoded, ack_samples = send_receive(
            relay_modem, proxy_modem, ack_frame.encode())
        ack_ok = (ack_decoded is not None and
                  ack_decoded.frame_type == FrameType.ACK)
        m_ack = compute_metrics(ack_samples, proxy_modem.sample_rate, baud_rate)

        wp2 = None
        if not ack_ok and ack_samples is not None:
            wp2 = os.path.join(wav_dir, f'{tag}_ack_{i:02d}.wav')
            save_wav(ack_samples, proxy_modem.sample_rate, wp2)

        fmt_result(tag, i, ack_ok, m_ack,
                   extra=f'ACK proxy_qd={proxy_qd}', wav_path=wp2)

        results.append({
            'frame': i, 'data_ok': data_ok, 'ack_ok': ack_ok,
            'relay_queue_depth': relay_qd, 'proxy_queue_depth': proxy_qd,
        })
        time.sleep(0.02)

    return results


# ===== T6: RX queue accumulation =====

def test_queue_accumulation(proxy_modem, relay_modem, num_frames, baud_rate, wav_dir):
    """Monitor RX queue depth growth during sustained bidirectional exchange."""
    tag = 'T6-queue'
    print(f'\n=== T6: RX queue accumulation ({num_frames} round trips) ===')
    results = []

    for i in range(num_frames):
        relay_qd_pre = relay_modem.audio._rx_queue.qsize()
        proxy_qd_pre = proxy_modem.audio._rx_queue.qsize()

        # DATA
        data_frame = Frame.create_data(i, FIXED_PAYLOAD)
        decoded, rx_samples = send_receive(
            proxy_modem, relay_modem, data_frame.encode())
        data_ok = (decoded is not None and decoded.payload == data_frame.payload)
        m = compute_metrics(rx_samples, relay_modem.sample_rate, baud_rate)

        relay_qd_post = relay_modem.audio._rx_queue.qsize()

        wp = None
        if not data_ok and rx_samples is not None:
            wp = os.path.join(wav_dir, f'{tag}_data_{i:02d}.wav')
            save_wav(rx_samples, relay_modem.sample_rate, wp)

        fmt_result(tag, i, data_ok, m,
                   extra=f'DATA q_pre={relay_qd_pre} q_post={relay_qd_post}',
                   wav_path=wp)

        # ACK
        ack_frame = Frame.create_ack(i)
        ack_decoded, ack_samples = send_receive(
            relay_modem, proxy_modem, ack_frame.encode())
        ack_ok = (ack_decoded is not None and
                  ack_decoded.frame_type == FrameType.ACK)
        m_ack = compute_metrics(ack_samples, proxy_modem.sample_rate, baud_rate)

        proxy_qd_post = proxy_modem.audio._rx_queue.qsize()

        wp2 = None
        if not ack_ok and ack_samples is not None:
            wp2 = os.path.join(wav_dir, f'{tag}_ack_{i:02d}.wav')
            save_wav(ack_samples, proxy_modem.sample_rate, wp2)

        fmt_result(tag, i, ack_ok, m_ack,
                   extra=f'ACK q_pre={proxy_qd_pre} q_post={proxy_qd_post}',
                   wav_path=wp2)

        results.append({
            'frame': i,
            'data_ok': data_ok, 'ack_ok': ack_ok,
            'relay_qd_pre': relay_qd_pre, 'relay_qd_post': relay_qd_post,
            'proxy_qd_pre': proxy_qd_pre, 'proxy_qd_post': proxy_qd_post,
        })
        time.sleep(0.02)

    # Report queue depth trend
    if results:
        relay_qds = [r['relay_qd_pre'] for r in results]
        proxy_qds = [r['proxy_qd_pre'] for r in results]
        print(f'\n  Queue depth trend (pre-receive):')
        print(f'    Relay: min={min(relay_qds)} max={max(relay_qds)} '
              f'last={relay_qds[-1]}')
        print(f'    Proxy: min={min(proxy_qds)} max={max(proxy_qds)} '
              f'last={proxy_qds[-1]}')

    return results


# ===== T7: Large unidirectional (plain HTTP simulation) =====

def test_large_unidirectional(proxy_modem, relay_modem, num_frames, baud_rate, wav_dir):
    """Send 15+ frames in one direction to exceed the frame-8 threshold.
    If this passes, degradation is CONNECT/TLS-specific, not frame-count."""
    # Use at least 15 frames to exceed frame-8 threshold
    count = max(15, num_frames)
    tag = 'T7-large'
    print(f'\n=== T7: Large unidirectional ({count} frames, proxy->relay) ===')
    results = []

    for i in range(count):
        frame = Frame.create_data(i, FIXED_PAYLOAD)
        decoded, rx_samples = send_receive(
            proxy_modem, relay_modem, frame.encode())
        success = (decoded is not None and decoded.payload == frame.payload)
        m = compute_metrics(rx_samples, relay_modem.sample_rate, baud_rate)

        wp = None
        if not success and rx_samples is not None:
            wp = os.path.join(wav_dir, f'{tag}_frame{i:02d}.wav')
            save_wav(rx_samples, relay_modem.sample_rate, wp)

        fmt_result(tag, i, success, m, wav_path=wp)
        results.append({'frame': i, 'success': success, **m})

        # Tight timing like real HTTP response streaming
        time.sleep(0.05)

    # Also test relay->proxy (Cable 2, the problematic direction)
    print(f'\n  --- Cable 2: relay->proxy ({count} frames) ---')
    for i in range(count):
        frame = Frame.create_data(i + count, FIXED_PAYLOAD)
        decoded, rx_samples = send_receive(
            relay_modem, proxy_modem, frame.encode())
        success = (decoded is not None and decoded.payload == frame.payload)
        m = compute_metrics(rx_samples, proxy_modem.sample_rate, baud_rate)

        wp = None
        if not success and rx_samples is not None:
            wp = os.path.join(wav_dir, f'{tag}_cable2_frame{i:02d}.wav')
            save_wav(rx_samples, proxy_modem.sample_rate, wp)

        fmt_result(tag, i + count, success, m, extra='cable2', wav_path=wp)
        results.append({'frame': i + count, 'success': success,
                        'direction': 'cable2', **m})
        time.sleep(0.05)

    return results


# ===== Summary =====

def summarize(test_name, results):
    """Print summary for a test."""
    if not results:
        print(f'  {test_name}: no results')
        return

    # Handle different result formats
    if isinstance(results, dict):
        # T3: payload dict
        for name, payload_results in results.items():
            passed = sum(1 for r in payload_results if r.get('success', True))
            total = len(payload_results)
            fails = [r['frame'] for r in payload_results if not r.get('success', True)]
            status = f'{passed}/{total}'
            if fails:
                status += f' (fails at: {fails})'
            print(f'  {test_name}/{name}: {status}')
        return

    # Check for ARQ-style results (data_ok + ack_ok)
    if 'data_ok' in results[0]:
        data_pass = sum(1 for r in results if r['data_ok'])
        ack_pass = sum(1 for r in results if r['ack_ok'])
        total = len(results)
        data_fails = [r['frame'] for r in results if not r['data_ok']]
        ack_fails = [r['frame'] for r in results if not r['ack_ok']]
        line = f'  {test_name}: DATA {data_pass}/{total}, ACK {ack_pass}/{total}'
        if data_fails:
            line += f' data_fails={data_fails}'
        if ack_fails:
            line += f' ack_fails={ack_fails}'
        print(line)
        return

    # Simple success/fail
    passed = sum(1 for r in results if r.get('success', True))
    total = len(results)
    fails = [r['frame'] for r in results if not r.get('success', True)]
    status = f'{passed}/{total}'
    if fails:
        status += f' (fails at: {fails})'
    print(f'  {test_name}: {status}')


class TeeWriter:
    """Write to both a file and the original stream."""

    def __init__(self, stream, log_file):
        self.stream = stream
        self.log_file = log_file

    def write(self, data):
        self.stream.write(data)
        self.log_file.write(data)

    def flush(self):
        self.stream.flush()
        self.log_file.flush()


def main():
    parser = argparse.ArgumentParser(
        description='Protocol-level VAC degradation diagnostics')
    parser.add_argument(
        '--tests', default='1,2,3,4,5,6,7',
        help='Comma-separated test numbers to run (default: 1,2,3,4,5,6,7)')
    parser.add_argument(
        '--frames', type=int, default=20,
        help='Frames per test (default: 20)')
    parser.add_argument(
        '--baud-rate', type=int, default=1200,
        help='Baud rate (default: 1200)')
    parser.add_argument(
        '--duplex', choices=['half', 'full'], default='full',
        help='Duplex mode (default: full)')
    parser.add_argument(
        '--wav-dir', default='wav',
        help='Directory for WAV dumps (default: wav)')
    parser.add_argument(
        '--log', default=None,
        help='Log file path (default: wav/diag2_YYYYMMDD_HHMMSS.log)')
    args = parser.parse_args()

    tests = [int(t) for t in args.tests.split(',')]
    full_duplex = args.duplex == 'full'

    # Set up log file — tee stdout and stderr to file
    os.makedirs(args.wav_dir, exist_ok=True)
    log_path = args.log or os.path.join(
        args.wav_dir, f'diag2_{time.strftime("%Y%m%d_%H%M%S")}.log')
    log_file = open(log_path, 'w')
    sys.stdout = TeeWriter(sys.__stdout__, log_file)
    sys.stderr = TeeWriter(sys.__stderr__, log_file)

    print(f'Logging to: {log_path}')
    print('=== Protocol-Level VAC Degradation Diagnostics ===')
    print(f'Tests: {tests}  Frames: {args.frames}  '
          f'Baud: {args.baud_rate}  Duplex: {args.duplex}')

    with vac_lock():
        profile = get_profile('cable')
        print(f'Profile: {profile.name} (tx_volume={profile.tx_volume})')

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
        time.sleep(1)

        all_results = {}

        try:
            if 1 in tests:
                all_results['T1-timing'] = test_timing_sweep(
                    proxy_modem, relay_modem, args.frames, args.baud_rate,
                    args.wav_dir)

            if 2 in tests:
                all_results['T2-arq'] = test_arq_simulation(
                    proxy_modem, relay_modem, args.frames, args.baud_rate,
                    args.wav_dir)

            if 3 in tests:
                all_results['T3-payload'] = test_tls_payloads(
                    proxy_modem, relay_modem, args.frames, args.baud_rate,
                    args.wav_dir)

            if 4 in tests:
                all_results['T4-handshake'] = test_handshake_warmup(
                    proxy_modem, relay_modem, args.frames, args.baud_rate,
                    args.wav_dir)

            if 5 in tests:
                all_results['T5-noise'] = test_noise_probe(
                    proxy_modem, relay_modem, args.frames, args.baud_rate,
                    args.wav_dir)

            if 6 in tests:
                all_results['T6-queue'] = test_queue_accumulation(
                    proxy_modem, relay_modem, args.frames, args.baud_rate,
                    args.wav_dir)

            if 7 in tests:
                all_results['T7-large'] = test_large_unidirectional(
                    proxy_modem, relay_modem, args.frames, args.baud_rate,
                    args.wav_dir)

        finally:
            proxy_modem.stop()
            relay_modem.stop()

        # Summary
        print('\n=== Summary ===')
        for name, results in all_results.items():
            summarize(name, results)

    print(f'\nResults saved to: {log_path}')
    log_file.close()
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
    return 0


if __name__ == '__main__':
    sys.exit(main())
