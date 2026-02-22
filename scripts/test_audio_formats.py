#!/usr/bin/env python3
"""Test different audio formats to find what works with DELL monitor."""
import time
import numpy as np
import sounddevice as sd

device = 5  # DELL MME
sr = 44100
duration = 0.5

print("=" * 60)
print("Testing different audio formats on DELL monitor")
print("=" * 60)

# Generate base tone
t = np.linspace(0, duration, int(sr * duration))
tone_mono = 0.5 * np.sin(2 * np.pi * 1000 * t)

def test_format(name, audio, dtype, channels):
    print(f"\n{name}:", end=" ", flush=True)
    try:
        start = time.time()
        sd.play(audio.astype(dtype), sr, device=device)
        sd.wait()
        elapsed = time.time() - start
        status = "OK!" if elapsed < 2.0 else f"SLOW ({elapsed:.1f}s)"
        print(f"{elapsed:.2f}s - {status}")
        return elapsed < 2.0
    except Exception as e:
        print(f"ERROR: {e}")
        return False

# Test 1: Original (mono float32)
test_format("Mono float32", tone_mono, np.float32, 1)

# Test 2: Mono int16
tone_int16 = (tone_mono * 32767).astype(np.int16)
test_format("Mono int16", tone_int16, np.int16, 1)

# Test 3: Stereo float32
tone_stereo = np.column_stack([tone_mono, tone_mono])
test_format("Stereo float32", tone_stereo, np.float32, 2)

# Test 4: Stereo int16
tone_stereo_int16 = (tone_stereo * 32767).astype(np.int16)
test_format("Stereo int16", tone_stereo_int16, np.int16, 2)

# Test 5: Using OutputStream with explicit settings
print(f"\nOutputStream stereo int16:", end=" ", flush=True)
try:
    audio = tone_stereo_int16
    start = time.time()
    with sd.OutputStream(device=device, samplerate=sr, channels=2,
                         dtype='int16', blocksize=4096) as stream:
        stream.write(audio)
    elapsed = time.time() - start
    status = "OK!" if elapsed < 2.0 else f"SLOW ({elapsed:.1f}s)"
    print(f"{elapsed:.2f}s - {status}")
except Exception as e:
    print(f"ERROR: {e}")

# Test 6: Non-blocking play with manual sleep
print(f"\nNon-blocking + sleep:", end=" ", flush=True)
try:
    start = time.time()
    sd.play(tone_stereo.astype(np.float32), sr, device=device, blocking=False)
    time.sleep(duration + 0.1)  # Just wait for expected duration
    sd.stop()
    elapsed = time.time() - start
    print(f"{elapsed:.2f}s - (slept {duration+0.1:.1f}s)")
except Exception as e:
    print(f"ERROR: {e}")

print("\n" + "=" * 60)
print("Testing pygame/SDL2")
print("=" * 60)

try:
    import os
    os.environ['SDL_AUDIODRIVER'] = 'directsound'

    import pygame
    pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=2048)

    # Stereo tone for pygame
    tone_pygame = np.column_stack([tone_int16, tone_int16])
    sound = pygame.sndarray.make_sound(tone_pygame)

    print(f"\nPygame DirectSound stereo:", end=" ", flush=True)
    start = time.time()
    sound.play()
    while pygame.mixer.get_busy():
        time.sleep(0.01)
    elapsed = time.time() - start
    status = "OK!" if elapsed < 2.0 else f"SLOW ({elapsed:.1f}s)"
    print(f"{elapsed:.2f}s - {status}")

    pygame.mixer.quit()
except ImportError:
    print("\nPygame not installed")
except Exception as e:
    print(f"\nPygame ERROR: {e}")

print("\n" + "=" * 60)
print("Done!")
print("=" * 60)
