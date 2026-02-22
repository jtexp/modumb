#!/usr/bin/env python3
"""Diagnostic: test frame exchange between two modem instances.

Tests the exact modem.receive() pipeline with two VAC cables.
Uses the same device config as the proxy/relay.

Proxy modem:  output=11 (VAC Cable 1 Out), input=5 (VAC Cable 2 In)
Relay modem:  output=8 (VAC Cable 2 Out), input=3 (VAC Cable 1 In)

Usage:
    .venv/Scripts/python.exe C:/Users/John/modumb/scripts/diag_modem_exchange.py
"""
import sys
import os
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import sounddevice as sd
from modumb.modem.modem import Modem
from modumb.modem.profiles import get_profile
from modumb.datalink.frame import Frame
from modumb.datalink.framer import Framer

# Device mapping — two VAC cables (same as test_e2e_vbcable.py)
PROXY_OUTPUT = 11   # VAC Cable 1 Line Out (proxy TX)
PROXY_INPUT = 5     # VAC Cable 2 Line In (proxy RX)
RELAY_OUTPUT = 8    # VAC Cable 2 Line Out (relay TX)
RELAY_INPUT = 3     # VAC Cable 1 Line In (relay RX)

def print_dev_info():
    """Print audio device info and sample rate support."""
    for idx, name_str, is_output in [
        (PROXY_OUTPUT, "Proxy TX", True), (PROXY_INPUT, "Proxy RX", False),
        (RELAY_OUTPUT, "Relay TX", True), (RELAY_INPUT, "Relay RX", False),
    ]:
        info = sd.query_devices(idx)
        rates = []
        for rate in [44100, 48000]:
            try:
                if is_output:
                    sd.check_output_settings(device=idx, samplerate=rate, channels=1)
                else:
                    sd.check_input_settings(device=idx, samplerate=rate, channels=1)
                rates.append(str(rate))
            except Exception:
                pass
        print(f'  {name_str}: dev={idx} "{info["name"]}" '
              f'default={int(info["default_samplerate"])}Hz '
              f'supported=[{",".join(rates)}]')


def test_modem_send_receive(label, tx_modem, rx_modem, frame):
    """Send a frame from tx_modem and receive on rx_modem."""
    print(f'\n--- {label} ---')

    frame_bytes = frame.encode()
    print(f'Frame: type={frame.frame_type.name} seq={frame.sequence} '
          f'payload={len(frame.payload)}B encoded={len(frame_bytes)}B')
    print(f'TX modem: rate={tx_modem.sample_rate}Hz, volume={tx_modem.tx_volume}')
    print(f'RX modem: rate={rx_modem.sample_rate}Hz')

    # Start receive in background thread
    result = {'data': None, 'error': None}

    def rx_thread():
        try:
            print(f'  RX: Listening (timeout=8s)...', flush=True)
            data = rx_modem.receive(timeout=8.0)
            result['data'] = data
            if data:
                print(f'  RX: Got {len(data)} bytes', flush=True)
            else:
                print(f'  RX: Timeout (no data)', flush=True)
        except Exception as e:
            result['error'] = str(e)
            print(f'  RX: Error: {e}', flush=True)

    rx_t = threading.Thread(target=rx_thread)
    rx_t.start()

    # Wait a moment for receiver to be ready, then send
    time.sleep(1.0)
    print(f'  TX: Sending frame...', flush=True)
    tx_modem.send(frame_bytes, blocking=True)
    print(f'  TX: Done', flush=True)

    # Wait for receiver
    rx_t.join(timeout=15)

    if result['error']:
        print(f'FAIL: RX error: {result["error"]}')
        return False

    if result['data'] is None or len(result['data']) == 0:
        print(f'FAIL: No data received')
        return False

    raw_bytes = result['data']
    print(f'Received: {len(raw_bytes)} bytes')
    print(f'Hex (first 60): {raw_bytes[:60].hex()}')

    # Try to decode frame
    decoded = Frame.decode(raw_bytes)
    if decoded is not None:
        print(f'Decoded: type={decoded.frame_type.name} seq={decoded.sequence} '
              f'payload={len(decoded.payload)}B')
        if (decoded.frame_type == frame.frame_type and
            decoded.sequence == frame.sequence and
            decoded.payload == frame.payload):
            print('PASS: Frame matches!')
            return True
        else:
            print(f'FAIL: Frame content mismatch')
            return False
    else:
        print(f'FAIL: Could not decode frame')
        # Show comparison
        print(f'  Expected hex: {frame_bytes[:50].hex()}')
        print(f'  Got hex:      {raw_bytes[:50].hex()}')
        return False


def main():
    print('=== Modem-to-Modem Exchange Diagnostic ===')
    print_dev_info()

    profile = get_profile('cable')
    print(f'\nProfile: {profile.name} (tx_volume={profile.tx_volume}, '
          f'lead_silence={profile.lead_silence}, trail_silence={profile.trail_silence})')

    # Create proxy modem (output=11 VAC1, input=5 VAC2)
    proxy_modem = Modem(
        input_device=PROXY_INPUT,
        output_device=PROXY_OUTPUT,
        profile=profile,
    )
    print(f'\nProxy modem: rate={proxy_modem.sample_rate}Hz')

    # Create relay modem (output=8 VAC2, input=3 VAC1)
    relay_modem = Modem(
        input_device=RELAY_INPUT,
        output_device=RELAY_OUTPUT,
        profile=profile,
    )
    print(f'Relay modem: rate={relay_modem.sample_rate}Hz')

    # Start both modems
    proxy_modem.start()
    relay_modem.start()
    time.sleep(1)  # Let streams settle

    results = {}

    try:
        # Test 1: Proxy -> VAC Cable 1 -> Relay (SYN frame)
        syn = Frame.create_syn()
        results['SYN: proxy->relay'] = test_modem_send_receive(
            'Proxy -> Relay (SYN via VAC Cable 1)',
            proxy_modem, relay_modem, syn)

        time.sleep(1)

        # Test 2: Relay -> VAC Cable 2 -> Proxy (SYN-ACK frame)
        syn_ack = Frame.create_syn_ack()
        results['SYN-ACK: relay->proxy'] = test_modem_send_receive(
            'Relay -> Proxy (SYN-ACK via VAC Cable 2)',
            relay_modem, proxy_modem, syn_ack)

        time.sleep(1)

        # Test 3: Proxy -> VAC Cable 1 -> Relay (DATA frame, 64B)
        payload = b'GET http://example.com HTTP/1.1\r\nHost: example.com\r\n\r\nPad!!'
        payload = payload[:64].ljust(64, b'.')
        data_frame = Frame.create_data(1, payload)
        results['DATA: proxy->relay'] = test_modem_send_receive(
            'Proxy -> Relay (DATA 64B via VAC Cable 1)',
            proxy_modem, relay_modem, data_frame)

    finally:
        proxy_modem.stop()
        relay_modem.stop()

    # Summary
    print('\n=== Summary ===')
    for test, passed in results.items():
        print(f'  {test}: {"PASS" if passed else "FAIL"}')

    return 0 if all(results.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
