#!/usr/bin/env python3
"""Diagnostic: send a frame through VB-Cable and check demodulation quality.

Tests the raw modulate → VB-Cable → demodulate path for bit errors.
Uses device 7 (VB-Cable output) → device 3 (VB-Cable input).

Usage:
    .venv/Scripts/python.exe C:/Users/John/modumb/scripts/diag_vbcable_frame.py
"""
import sys
import os
import time
import queue
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import numpy as np
import sounddevice as sd
from modumb.modem.afsk import AFSKModulator, AFSKDemodulator, SAMPLE_RATE, BAUD_RATE
from modumb.datalink.frame import Frame

OUT_DEV = 7   # VB-Cable Input (output device)
IN_DEV = 3    # VB-Cable Output (input device)
TX_VOLUME = 0.5

def check_rate(dev, rate, kind):
    """Check if a device supports a given sample rate."""
    try:
        if kind == 'output':
            sd.check_output_settings(device=dev, samplerate=rate, channels=1)
        else:
            sd.check_input_settings(device=dev, samplerate=rate, channels=1)
        return True
    except Exception:
        return False

def test_frame_roundtrip(sample_rate, frame, label):
    """Test modulate → VB-Cable → demodulate for a single frame."""
    frame_bytes = frame.encode()
    print(f'\n--- {label} @ {sample_rate} Hz ---')
    print(f'Frame: type={frame.frame_type.name} seq={frame.sequence} '
          f'payload={len(frame.payload)}B encoded={len(frame_bytes)}B')

    # Modulate
    mod = AFSKModulator(sample_rate=sample_rate, baud_rate=BAUD_RATE)
    samples = mod.modulate(frame_bytes)
    samples = (samples * TX_VOLUME).astype(np.float32)

    # Add lead/trail silence
    lead = np.zeros(int(0.1 * sample_rate), dtype=np.float32)
    trail = np.zeros(int(0.2 * sample_rate), dtype=np.float32)
    tx_signal = np.concatenate([lead, samples, trail])

    tx_duration = len(tx_signal) / sample_rate
    print(f'TX: {len(tx_signal)} samples ({tx_duration:.2f}s)')

    # Start recording BEFORE playing
    rx_chunks = queue.Queue()
    def rx_callback(indata, frames, time_info, status):
        rx_chunks.put(indata.copy().flatten())

    rx_stream = sd.InputStream(
        samplerate=sample_rate, channels=1, device=IN_DEV,
        dtype='float32', blocksize=1024, callback=rx_callback)
    rx_stream.start()
    time.sleep(0.2)  # Let the stream settle

    # Play audio
    sd.play(tx_signal, sample_rate, device=OUT_DEV, blocking=True)
    time.sleep(0.5)  # Wait for trailing audio to arrive

    # Stop recording
    rx_stream.stop()
    rx_stream.close()

    # Collect recorded audio
    all_rx = []
    while not rx_chunks.empty():
        all_rx.append(rx_chunks.get())

    if not all_rx:
        print('FAIL: No audio recorded')
        return False

    rx_audio = np.concatenate(all_rx)
    rx_rms = float(np.sqrt(np.mean(rx_audio ** 2)))
    print(f'RX: {len(rx_audio)} samples ({len(rx_audio)/sample_rate:.2f}s) RMS={rx_rms:.4f}')

    # Trim leading silence (same as modem.receive fix)
    abs_rx = np.abs(rx_audio)
    max_amp = float(np.max(abs_rx))
    if max_amp > 0.005:
        threshold = max_amp * 0.1
        above = np.where(abs_rx > threshold)[0]
        if len(above) > 0:
            spb = sample_rate // BAUD_RATE
            margin = spb * 8
            start = max(0, int(above[0]) - margin)
            trimmed = len(rx_audio) - (len(rx_audio) - start)
            rx_audio = rx_audio[start:]
            print(f'Trimmed {trimmed} leading silence samples ({trimmed/sample_rate*1000:.0f}ms)')

    # Demodulate
    demod = AFSKDemodulator(sample_rate=sample_rate, baud_rate=BAUD_RATE)
    raw_bytes = demod.demodulate(rx_audio)
    print(f'Demodulated: {len(raw_bytes)} bytes')
    print(f'Hex (first 60): {raw_bytes[:60].hex()}')

    # Try to decode frame
    decoded = Frame.decode(raw_bytes)
    if decoded is not None:
        print(f'Frame decoded: type={decoded.frame_type.name} seq={decoded.sequence} '
              f'payload={len(decoded.payload)}B')
        # Compare
        if (decoded.frame_type == frame.frame_type and
            decoded.sequence == frame.sequence and
            decoded.payload == frame.payload):
            print('PASS: Frame matches!')
            return True
        else:
            print('FAIL: Frame content mismatch')
            if decoded.frame_type != frame.frame_type:
                print(f'  type: expected {frame.frame_type.name} got {decoded.frame_type.name}')
            if decoded.sequence != frame.sequence:
                print(f'  seq: expected {frame.sequence} got {decoded.sequence}')
            if decoded.payload != frame.payload:
                print(f'  payload differs')
            return False
    else:
        print('FAIL: Could not decode frame (CRC or sync error)')

        # Byte-by-byte comparison of raw demodulated vs expected
        expected_hex = frame_bytes.hex()
        got_hex = raw_bytes[:len(frame_bytes)+10].hex()
        print(f'Expected hex: {expected_hex[:100]}...')
        print(f'Got hex:      {got_hex[:100]}...')

        # Find first difference
        for i in range(min(len(frame_bytes), len(raw_bytes))):
            if i < len(raw_bytes) and frame_bytes[i] != raw_bytes[i]:
                print(f'First difference at byte {i}: expected 0x{frame_bytes[i]:02x} got 0x{raw_bytes[i]:02x}')
                # Show surrounding context
                start = max(0, i - 3)
                end = min(len(frame_bytes), i + 4)
                print(f'  Expected [{start}:{end}]: {frame_bytes[start:end].hex()}')
                if end <= len(raw_bytes):
                    print(f'  Got      [{start}:{end}]: {raw_bytes[start:end].hex()}')
                break

        return False


def main():
    print('=== VB-Cable Frame Roundtrip Diagnostic ===')
    print(f'Output device: {OUT_DEV} ({sd.query_devices(OUT_DEV)["name"]})')
    print(f'Input device:  {IN_DEV} ({sd.query_devices(IN_DEV)["name"]})')

    # Check supported rates
    for rate in [44100, 48000]:
        out_ok = check_rate(OUT_DEV, rate, 'output')
        in_ok = check_rate(IN_DEV, rate, 'input')
        print(f'{rate} Hz: output={out_ok} input={in_ok}')

    results = {}

    # Test at both sample rates
    for sr in [48000, 44100]:
        # Test 1: SYN frame (small, no payload)
        syn = Frame.create_syn()
        results[f'SYN@{sr}'] = test_frame_roundtrip(sr, syn, f'SYN frame')

        # Test 2: DATA frame with short payload
        short_data = Frame.create_data(0, b'Hello VB-Cable!')
        results[f'SHORT@{sr}'] = test_frame_roundtrip(sr, short_data, f'Short DATA frame')

        # Test 3: DATA frame with max payload (64 bytes)
        long_payload = b'GET http://example.com HTTP/1.1\r\nHost: example.com\r\n\r\nPadding!'
        long_payload = long_payload[:64].ljust(64, b'.')
        long_data = Frame.create_data(1, long_payload)
        results[f'LONG@{sr}'] = test_frame_roundtrip(sr, long_data, f'Long DATA frame (64B)')

    # Summary
    print('\n=== Summary ===')
    for test, passed in results.items():
        print(f'  {test}: {"PASS" if passed else "FAIL"}')

    return 0 if all(results.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
