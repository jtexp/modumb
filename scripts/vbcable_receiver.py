"""Receive a framed message through VB-Cable (run this BEFORE the sender)."""
import sys, os, time, queue
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import numpy as np
import sounddevice as sd
from modumb.modem.afsk import AFSKDemodulator, BAUD_RATE
from modumb.datalink.frame import Frame

IN_DEV = 3    # VB-CABLE Output (DirectSound input)
SR = 44100

# Record via InputStream callback
chunks = queue.Queue()
def callback(indata, frames, time_info, status):
    chunks.put(indata.copy())

print('Listening on VB-Cable... (15s timeout)', flush=True)
stream = sd.InputStream(samplerate=SR, channels=1, device=IN_DEV,
                        dtype='float32', blocksize=1024, callback=callback)
stream.start()

# Wait for signal (detect by RMS)
deadline = time.time() + 15
signal_detected = False
silence_count = 0
all_chunks = []

while time.time() < deadline:
    time.sleep(0.05)
    new_chunks = []
    while not chunks.empty():
        new_chunks.append(chunks.get())
    if not new_chunks:
        if signal_detected:
            silence_count += 1
            if silence_count > 10:  # ~0.5s silence after signal
                break
        continue

    for chunk in new_chunks:
        all_chunks.append(chunk)
        rms = float(np.sqrt(np.mean(chunk**2)))
        if rms > 0.01:
            signal_detected = True
            silence_count = 0

stream.stop()
stream.close()

if not all_chunks:
    print('TIMEOUT: No audio received')
    sys.exit(1)

audio = np.concatenate(all_chunks).flatten()
print(f'Recorded: {len(audio)} samples ({len(audio)/SR:.2f}s), '
      f'RMS={float(np.sqrt(np.mean(audio**2))):.4f}', flush=True)

# Demodulate
demod = AFSKDemodulator(sample_rate=SR, baud_rate=BAUD_RATE)
raw_bytes = demod.demodulate(audio)
print(f'Demodulated: {len(raw_bytes)} bytes', flush=True)

# Try to decode a frame from the raw bytes
frame = Frame.decode(raw_bytes)
if frame is not None:
    print(f'Frame type={frame.frame_type.name}, seq={frame.sequence}')
    print(f'Payload: {frame.payload!r}')
    print('SUCCESS: Framed roundtrip through VB-Cable!')
else:
    print(f'Raw bytes (hex): {raw_bytes[:80].hex()}')
    print('Could not decode frame (CRC or sync error)')
