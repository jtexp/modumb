#!/usr/bin/env python3
"""Test modem with DATA frames containing actual payload."""
import time, sys, os
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import sounddevice as sd
from modumb.modem.audio_io import AudioInterface
from modumb.modem.modem import Modem
from modumb.datalink.frame import Frame, FrameType

output_device = int(os.environ.get('MODEM_OUTPUT_DEVICE', '5'))
input_device = int(os.environ.get('MODEM_INPUT_DEVICE', '1'))

print("=== Data Frame Round-Trip Test ===\n")

tx_audio = AudioInterface(input_device=input_device, output_device=output_device)
tx_modem = Modem(audio=tx_audio)
rx_audio = AudioInterface(input_device=input_device, output_device=output_device)
rx_modem = Modem(audio=rx_audio)

print(f"Sample rate: {tx_modem.sample_rate} Hz")

# Test frames with increasing payload sizes
test_payloads = [
    b"Hi",
    b"Hello!",
    b"The quick brown fox",
    b"git clone modem://audio/repo",
]

for payload in test_payloads:
    print(f"\n--- Payload: {payload!r} ({len(payload)} bytes) ---")

    frame = Frame(FrameType.DATA, sequence=1, payload=payload)
    frame_bytes = frame.encode()
    print(f"  Frame: {len(frame_bytes)} bytes")

    tx_samples = tx_modem.modulator.modulate(frame_bytes) * 0.08
    lead = np.zeros(int(0.3 * tx_modem.sample_rate), dtype=np.float32)
    trail = np.zeros(int(0.2 * tx_modem.sample_rate), dtype=np.float32)
    tx_samples = np.concatenate([lead, tx_samples, trail])

    duration = len(tx_samples) / tx_modem.sample_rate
    print(f"  Audio: {duration:.2f}s")

    rx_audio.start()
    time.sleep(0.2)
    rx_audio.clear_receive_buffer()

    sd.play(tx_samples, tx_modem.sample_rate, device=output_device)
    sd.wait()
    time.sleep(0.5)

    all_samples = []
    while True:
        try:
            block = rx_audio._rx_queue.get_nowait()
            all_samples.append(block)
        except:
            break
    rx_audio.stop()

    if not all_samples:
        print("  No audio recorded!")
        continue

    recorded = np.concatenate(all_samples)
    print(f"  Recorded: {len(recorded)} samples, Peak={np.max(np.abs(recorded)):.3f}")

    demod_bytes = rx_modem.demodulator.demodulate(recorded)
    print(f"  Demodulated: {len(demod_bytes)} bytes")

    decoded = Frame.decode(demod_bytes)
    if decoded:
        print(f"  *** DECODED: type={decoded.frame_type.name} seq={decoded.sequence}")
        print(f"  Payload: {decoded.payload!r}")
        if decoded.payload == payload:
            print(f"  PAYLOAD MATCHES!")
        else:
            print(f"  MISMATCH! Expected: {payload!r}")
    else:
        print(f"  Frame decode FAILED")
        if len(demod_bytes) > 0:
            print(f"  Hex: {demod_bytes[:40].hex()}")

    time.sleep(0.5)

print("\n=== Done ===")
