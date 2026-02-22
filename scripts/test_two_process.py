#!/usr/bin/env python3
"""Test if two processes can share the same mic on Windows.

Process 1 (receiver): Opens mic, listens for frames
Process 2 (sender): Opens mic+speaker, sends a SYN frame

This tests whether sharing the UMIK-1 between two processes works.
"""
import sys, os, time, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

MODE = sys.argv[1] if len(sys.argv) > 1 else 'sender'

output_device = int(os.environ.get('MODEM_OUTPUT_DEVICE', '5'))
input_device = int(os.environ.get('MODEM_INPUT_DEVICE', '1'))

if MODE == 'receiver':
    # Receiver process: open mic and listen
    from modumb.modem.modem import Modem
    from modumb.datalink.frame import Frame

    modem = Modem(input_device=input_device, output_device=output_device)
    modem.start()
    time.sleep(0.3)
    modem.audio.clear_receive_buffer()

    print(f"RECEIVER: Listening on device {input_device}...", flush=True)

    for attempt in range(5):
        print(f"RECEIVER: Waiting for frame (attempt {attempt+1})...", flush=True)
        data = modem.receive(timeout=8.0)
        if len(data) > 0:
            print(f"RECEIVER: Got {len(data)} bytes: {data[:30].hex()}", flush=True)
            frame = Frame.decode(data)
            if frame:
                print(f"RECEIVER: *** FRAME DECODED: {frame.frame_type.name} ***", flush=True)
                break
            else:
                print(f"RECEIVER: Frame decode failed", flush=True)
                # Check preamble
                aa = sum(1 for b in data[:20] if b == 0xAA)
                print(f"RECEIVER: Preamble AA count: {aa}/16", flush=True)
        else:
            print(f"RECEIVER: No data (timeout)", flush=True)

    modem.stop()

elif MODE == 'sender':
    # Sender: send SYN frame after a delay
    from modumb.modem.modem import Modem
    from modumb.datalink.frame import Frame

    modem = Modem(input_device=input_device, output_device=output_device)
    modem.start()

    print(f"SENDER: Will send SYN in 3 seconds...", flush=True)
    time.sleep(3.0)

    syn = Frame.create_syn()
    frame_bytes = syn.encode()
    print(f"SENDER: Sending SYN frame ({len(frame_bytes)} bytes, volume={modem.tx_volume})", flush=True)
    modem.send(frame_bytes)
    print(f"SENDER: SYN sent!", flush=True)

    time.sleep(1.0)
    modem.stop()

elif MODE == 'both':
    # Single process: send then receive via separate modem instance
    from modumb.modem.modem import Modem
    from modumb.datalink.frame import Frame

    # Single modem
    modem = Modem(input_device=input_device, output_device=output_device)
    modem.start()
    time.sleep(0.3)
    modem.audio.clear_receive_buffer()

    # Send
    syn = Frame.create_syn()
    frame_bytes = syn.encode()
    print(f"Sending SYN ({len(frame_bytes)} bytes, volume={modem.tx_volume})...", flush=True)
    modem.send(frame_bytes)
    print(f"SYN sent, now receiving...", flush=True)

    # Can't receive our own frame (echo suppressed)
    # But test that modem.receive works at all
    data = modem.receive(timeout=3.0)
    print(f"Received: {len(data)} bytes", flush=True)
    if len(data) > 0:
        print(f"Hex: {data[:30].hex()}", flush=True)

    modem.stop()

elif MODE == 'selftest':
    # Self-test: modulate a SYN, play it directly, record and demodulate
    # This is what our test scripts do - should work
    import numpy as np
    import sounddevice as sd
    from modumb.modem.audio_io import AudioInterface
    from modumb.modem.afsk import AFSKModulator, AFSKDemodulator
    from modumb.datalink.frame import Frame

    dev_info = sd.query_devices(output_device)
    sr = int(dev_info['default_samplerate'])

    mod = AFSKModulator(sample_rate=sr)
    demod = AFSKDemodulator(sample_rate=sr)

    syn = Frame.create_syn()
    frame_bytes = syn.encode()
    tx_samples = mod.modulate(frame_bytes) * 0.08
    lead = np.zeros(int(0.3 * sr), dtype=np.float32)
    trail = np.zeros(int(0.2 * sr), dtype=np.float32)
    tx_samples = np.concatenate([lead, tx_samples, trail])

    audio = AudioInterface(input_device=input_device, output_device=output_device)
    audio.start()
    time.sleep(0.2)
    audio.clear_receive_buffer()

    print(f"Playing SYN frame (same params as Modem.send)...", flush=True)
    sd.play(tx_samples, sr, device=output_device)
    sd.wait()
    time.sleep(0.5)

    all_samples = []
    while True:
        try:
            block = audio._rx_queue.get_nowait()
            all_samples.append(block)
        except:
            break
    audio.stop()

    if all_samples:
        recorded = np.concatenate(all_samples)
        print(f"Recorded: {len(recorded)} samples", flush=True)
        data = demod.demodulate(recorded)
        print(f"Demodulated: {len(data)} bytes", flush=True)
        if len(data) > 0:
            print(f"Hex: {data[:30].hex()}", flush=True)
            frame = Frame.decode(data)
            if frame:
                print(f"*** FRAME DECODED: {frame.frame_type.name} ***", flush=True)
            else:
                aa = sum(1 for b in data[:20] if b == 0xAA)
                print(f"Decode failed. AA={aa}/16", flush=True)
    else:
        print("No audio recorded!", flush=True)

elif MODE == 'modem_selftest':
    # Use Modem.send() and then manual receive (to test modem send path)
    import numpy as np
    from modumb.modem.modem import Modem
    from modumb.modem.afsk import AFSKDemodulator
    from modumb.datalink.frame import Frame

    modem = Modem(input_device=input_device, output_device=output_device)
    demod = AFSKDemodulator(sample_rate=modem.sample_rate)

    modem.start()
    time.sleep(0.3)

    # Clear echo suppression so we CAN hear ourselves
    # (Normally this is suppressed, but we want to test the modem path)

    syn = Frame.create_syn()
    frame_bytes = syn.encode()
    print(f"Sending via Modem.send() (volume={modem.tx_volume})...", flush=True)

    # Disable echo suppression temporarily
    modem.audio._echo_guard_time = 0.0

    modem.send(frame_bytes)
    print(f"Sent. Now reading raw audio...", flush=True)

    time.sleep(0.3)

    all_samples = []
    while True:
        try:
            block = modem.audio._rx_queue.get_nowait()
            all_samples.append(block)
        except:
            break

    if all_samples:
        recorded = np.concatenate(all_samples)
        print(f"Recorded: {len(recorded)} samples, Peak={np.max(np.abs(recorded)):.4f}", flush=True)
        data = demod.demodulate(recorded)
        print(f"Demodulated: {len(data)} bytes", flush=True)
        if len(data) > 0:
            print(f"Hex: {data[:30].hex()}", flush=True)
            frame = Frame.decode(data)
            if frame:
                print(f"*** FRAME DECODED: {frame.frame_type.name} ***", flush=True)
            else:
                aa = sum(1 for b in data[:20] if b == 0xAA)
                print(f"Decode failed. AA={aa}/16", flush=True)
    else:
        print("No audio captured (echo suppression might have blocked it)", flush=True)

    modem.stop()
