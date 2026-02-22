#!/usr/bin/env python3
import time, numpy as np, sounddevice as sd

sr = 44100
tone = (0.5 * np.sin(2 * 3.14159 * 1000 * np.linspace(0, 1, sr))).astype(np.float32)

print("Playing 1s tone via sounddevice (device 5, DELL)...")
start = time.time()
sd.play(tone, sr, device=5)
sd.wait()
elapsed = time.time() - start
print(f"Took: {elapsed:.2f}s - {'OK!' if elapsed < 2.0 else 'SLOW'}")
