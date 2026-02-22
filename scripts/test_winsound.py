#!/usr/bin/env python3
"""Test native Windows audio APIs."""
import time
import wave
import struct
import tempfile
import os

print("=" * 60)
print("Testing Native Windows Audio (no PortAudio)")
print("=" * 60)

# Generate a simple tone and save to WAV
sr = 44100
duration = 1.0
frequency = 1000

samples = []
for i in range(int(sr * duration)):
    t = i / sr
    sample = int(32767 * 0.5 * __import__('math').sin(2 * 3.14159 * frequency * t))
    samples.append(struct.pack('<h', sample))

# Save to temp WAV file
wav_path = os.path.join(tempfile.gettempdir(), 'test_tone.wav')
with wave.open(wav_path, 'w') as wav:
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(sr)
    wav.writeframes(b''.join(samples))

print(f"Created test WAV: {wav_path}")
print(f"Duration: {duration}s")
print()

# Test 1: winsound.PlaySound (native Windows MME)
print("Test 1: winsound.PlaySound (native MME)...", flush=True)
try:
    import winsound
    start = time.time()
    winsound.PlaySound(wav_path, winsound.SND_FILENAME)
    elapsed = time.time() - start
    status = "OK!" if elapsed < 2.0 else f"SLOW ({elapsed:.1f}s)"
    print(f"  Playback took: {elapsed:.2f}s - {status}")
except Exception as e:
    print(f"  ERROR: {e}")

print()

# Test 2: pygame
print("Test 2: pygame/SDL2...", flush=True)
try:
    import pygame
    pygame.mixer.init(frequency=sr, size=-16, channels=1)
    sound = pygame.mixer.Sound(wav_path)
    start = time.time()
    sound.play()
    while pygame.mixer.get_busy():
        time.sleep(0.01)
    elapsed = time.time() - start
    status = "OK!" if elapsed < 2.0 else f"SLOW ({elapsed:.1f}s)"
    print(f"  Playback took: {elapsed:.2f}s - {status}")
    pygame.mixer.quit()
except Exception as e:
    print(f"  ERROR: {e}")

print()

# Cleanup
os.unlink(wav_path)
print("Done!")
