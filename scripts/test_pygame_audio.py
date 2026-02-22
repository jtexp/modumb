#!/usr/bin/env python3
"""Test audio with pygame/SDL2."""
import time
import os
import numpy as np

# Try different SDL audio drivers
drivers = ['directsound', 'winmm', 'wasapi', 'disk']

print("Testing pygame/SDL2 audio...")
print()

for driver in drivers:
    os.environ['SDL_AUDIODRIVER'] = driver

    try:
        import pygame
        pygame.mixer.quit()  # Reset from previous iteration
        pygame.mixer.init(frequency=44100, size=-16, channels=1, buffer=1024)

        # Generate 1-second tone
        sr = 44100
        duration = 1.0
        t = np.linspace(0, duration, int(sr * duration), dtype=np.float32)
        tone = 0.5 * np.sin(2 * np.pi * 1000 * t)
        tone_int16 = (tone * 32767).astype(np.int16)

        # Create sound from array
        sound = pygame.sndarray.make_sound(tone_int16)

        print(f"Driver '{driver}':", end=" ", flush=True)
        start = time.time()
        sound.play()
        while pygame.mixer.get_busy():
            time.sleep(0.01)
        elapsed = time.time() - start

        pygame.mixer.quit()

        if abs(elapsed - duration) < 1.0:
            print(f"{elapsed:.2f}s - SUCCESS!")
        else:
            print(f"{elapsed:.2f}s - slow")

    except Exception as e:
        print(f"Driver '{driver}': ERROR - {e}")

print()
print("Done!")
