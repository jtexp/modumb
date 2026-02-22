#!/usr/bin/env python3
"""Test modem frame transmission and reception over real audio.

Runs a sender and receiver in separate threads on the same machine,
using real speakers (DELL monitor) and microphone (webcam).
"""
import time
import sys
import os
import threading
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from modumb.modem.audio_io import AudioInterface
from modumb.modem.modem import Modem
from modumb.datalink.frame import Frame, FrameType
from modumb.datalink.framer import Framer

output_device = int(os.environ.get('MODEM_OUTPUT_DEVICE', '5'))
input_device = int(os.environ.get('MODEM_INPUT_DEVICE', '1'))

print("=== Real Audio Modem Test ===\n")

# Create two separate modem instances: one for TX, one for RX
# TX modem only needs output device
tx_audio = AudioInterface(
    input_device=input_device,
    output_device=output_device,
)
tx_modem = Modem(audio=tx_audio)

# RX modem only needs input device
rx_audio = AudioInterface(
    input_device=input_device,
    output_device=output_device,
)
rx_modem = Modem(audio=rx_audio)

print(f"TX sample rate: {tx_modem.sample_rate} Hz")
print(f"RX sample rate: {rx_modem.sample_rate} Hz")
print(f"Output device: {output_device}")
print(f"Input device: {input_device}")
print()

# Test 1: Send a SYN frame and see if it can be received
print("--- Test 1: SYN Frame Round-Trip ---")

syn = Frame.create_syn()
frame_bytes = syn.encode()
print(f"SYN frame: {len(frame_bytes)} bytes")
print(f"  Hex: {frame_bytes.hex()}")

# Modulate
tx_samples = tx_modem.modulator.modulate(frame_bytes)
# Scale down volume significantly - webcam AGC boosts weak signals
VOLUME = 0.08
tx_samples = tx_samples * VOLUME
# Longer lead silence to let AGC stabilize
lead = np.zeros(int(0.3 * tx_modem.sample_rate), dtype=np.float32)
trail = np.zeros(int(0.2 * tx_modem.sample_rate), dtype=np.float32)
tx_samples = np.concatenate([lead, tx_samples, trail])
print(f"  Volume: {VOLUME} ({VOLUME*100:.0f}%)")

duration = len(tx_samples) / tx_modem.sample_rate
print(f"  Audio: {len(tx_samples)} samples ({duration:.2f}s)")

# Start recording in background
rx_audio.start()
time.sleep(0.2)  # Let input stream settle
rx_audio.clear_receive_buffer()

# Play the frame
import sounddevice as sd
print(f"\nPlaying SYN frame through speakers...")
start = time.time()
sd.play(tx_samples, tx_modem.sample_rate, device=output_device)
sd.wait()
elapsed = time.time() - start
print(f"  Playback took: {elapsed:.3f}s")

# Wait a bit for mic to pick up remaining audio
time.sleep(0.5)

# Read what the microphone heard
print(f"\nReading microphone buffer...")
all_samples = []
while True:
    try:
        block = rx_audio._rx_queue.get_nowait()
        all_samples.append(block)
    except:
        break

if all_samples:
    recorded = np.concatenate(all_samples)
    rms = np.sqrt(np.mean(recorded ** 2))
    peak = np.max(np.abs(recorded))
    print(f"  Recorded: {len(recorded)} samples ({len(recorded)/rx_modem.sample_rate:.2f}s)")
    print(f"  RMS: {rms:.4f}, Peak: {peak:.4f}")

    # Demodulate
    print(f"\nDemodulating...")
    demod_bytes = rx_modem.demodulator.demodulate(recorded)
    print(f"  Demodulated: {len(demod_bytes)} bytes")
    if len(demod_bytes) > 0:
        print(f"  Hex: {demod_bytes[:50].hex()}")

        # Count preamble bytes
        preamble_count = sum(1 for b in demod_bytes[:20] if b == 0xAA)
        print(f"  Preamble bytes (0xAA): {preamble_count}/16")

        # Try to decode as frame
        frame = Frame.decode(demod_bytes)
        if frame:
            print(f"\n  *** FRAME DECODED SUCCESSFULLY! ***")
            print(f"  Type: {frame.frame_type.name}")
            print(f"  Sequence: {frame.sequence}")
            print(f"  Payload: {frame.payload.hex() if frame.payload else '(empty)'}")
        else:
            print(f"\n  Frame decode FAILED (CRC or format error)")

            # Try with different offsets
            print(f"\n  Trying offset scan...")
            spb = rx_modem.demodulator.samples_per_bit
            best_preamble = 0
            best_offset = 0
            for offset in range(0, min(len(recorded), spb * 20), max(1, spb // 8)):
                d = rx_modem.demodulator._demodulate_raw(recorded[offset:])
                if len(d) > 0:
                    pc = sum(1 for b in d[:20] if b == 0xAA)
                    if pc > best_preamble:
                        best_preamble = pc
                        best_offset = offset
                        if pc >= 14:
                            # Also try frame decode
                            f = Frame.decode(d)
                            if f:
                                print(f"  *** FRAME DECODED at offset {offset}! ***")
                                print(f"  Type: {f.frame_type.name}")
                                break

            print(f"  Best preamble score: {best_preamble}/16 at offset {best_offset}")
            d = rx_modem.demodulator._demodulate_raw(recorded[best_offset:])
            if len(d) > 0:
                print(f"  Best demod ({len(d)} bytes): {d[:30].hex()}")
else:
    print("  No audio received! Check microphone.")

rx_audio.stop()
print("\n=== Done ===")
