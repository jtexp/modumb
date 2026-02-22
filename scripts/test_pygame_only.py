#!/usr/bin/env python3
import pygame
import numpy as np
import time

pygame.mixer.init(frequency=44100, size=-16, channels=2)
t = np.linspace(0, 1.0, 44100, dtype=np.float32)
tone_mono = (0.5 * np.sin(2 * 3.14159 * 1000 * t) * 32767).astype(np.int16)
tone = np.column_stack([tone_mono, tone_mono])  # Stereo
sound = pygame.sndarray.make_sound(tone)

print("Playing 1s tone via pygame/SDL2...")
start = time.time()
sound.play()
while pygame.mixer.get_busy():
    time.sleep(0.01)
elapsed = time.time() - start
print(f"Took: {elapsed:.2f}s")
pygame.mixer.quit()
