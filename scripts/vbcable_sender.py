"""Send a framed message through VB-Cable (run this AFTER the receiver)."""
import sys, os, time, queue
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import numpy as np
import sounddevice as sd
from modumb.modem.afsk import AFSKModulator, SAMPLE_RATE, BAUD_RATE

OUT_DEV = 7   # VB-CABLE Input (DirectSound output)
SR = 44100

from modumb.datalink.frame import Frame

payload = b'Hello from VB-Cable!'
frame = Frame.create_data(0, payload)
frame_bytes = frame.encode()

mod = AFSKModulator(sample_rate=SR, baud_rate=BAUD_RATE)
samples = mod.modulate(frame_bytes)
samples = (samples * 0.5).astype(np.float32)

# Add lead/trail silence
lead = np.zeros(int(0.1 * SR), dtype=np.float32)
trail = np.zeros(int(0.2 * SR), dtype=np.float32)
tx_signal = np.concatenate([lead, samples, trail])

print(f'Sending: {payload!r} ({len(tx_signal)} samples, {len(tx_signal)/SR:.2f}s)', flush=True)
time.sleep(1)  # Wait for receiver

sd.play(tx_signal, SR, device=OUT_DEV, blocking=True)
time.sleep(0.5)

print('Done.', flush=True)
