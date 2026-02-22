#!/usr/bin/env python3
"""Debug DELL HDMI audio timing issue."""
import time
import numpy as np
import sounddevice as sd

def generate_tone(freq, duration, sample_rate):
    """Generate a sine wave tone."""
    t = np.linspace(0, duration, int(sample_rate * duration), dtype=np.float32)
    return 0.5 * np.sin(2 * np.pi * freq * t)

def test_playback(device, sample_rate, duration=1.0, method="play"):
    """Test playback with specific settings."""
    tone = generate_tone(1000, duration, sample_rate)

    print(f"  Playing {duration}s at {sample_rate}Hz via {method}...", end=" ", flush=True)
    start = time.time()

    try:
        if method == "play":
            sd.play(tone, sample_rate, device=device)
            sd.wait()
        elif method == "stream":
            with sd.OutputStream(device=device, samplerate=sample_rate,
                                channels=1, dtype='float32') as stream:
                stream.write(tone)
        elif method == "stream_small_buffer":
            with sd.OutputStream(device=device, samplerate=sample_rate,
                                channels=1, dtype='float32',
                                blocksize=512, latency='low') as stream:
                stream.write(tone)
        elif method == "raw_stream":
            with sd.RawOutputStream(device=device, samplerate=sample_rate,
                                   channels=1, dtype='float32') as stream:
                stream.write(tone.tobytes())

        elapsed = time.time() - start
        status = "OK" if abs(elapsed - duration) < 1.0 else f"SLOW ({elapsed:.1f}s)"
        print(f"{elapsed:.2f}s - {status}")
        return elapsed
    except Exception as e:
        print(f"ERROR: {e}")
        return None

print("=" * 60)
print("DELL HDMI Audio Debug Test")
print("=" * 60)

# Find all DELL devices
print("\nDELL audio devices:")
dell_devices = []
for i, d in enumerate(sd.query_devices()):
    if "DELL" in d["name"] and d["max_output_channels"] > 0:
        api_name = sd.query_hostapis(d["hostapi"])["name"]
        print(f"  {i}: {d['name'][:40]} | {api_name} | {int(d['default_samplerate'])}Hz")
        dell_devices.append((i, d, api_name))

print("\n" + "=" * 60)
print("Testing each DELL device with different methods...")
print("=" * 60)

for device_id, dev_info, api_name in dell_devices:
    native_sr = int(dev_info['default_samplerate'])
    print(f"\nDevice {device_id} ({api_name}, {native_sr}Hz):")

    # Test with native sample rate first
    test_playback(device_id, native_sr, 0.5, "play")

    # If that's slow, don't bother with other methods for this device

print("\n" + "=" * 60)
print("Testing with explicit WASAPI settings...")
print("=" * 60)

# Try WASAPI device with exclusive mode settings
wasapi_devices = [(i, d) for i, d, api in dell_devices if api == "Windows WASAPI"]
for device_id, dev_info in wasapi_devices:
    native_sr = int(dev_info['default_samplerate'])
    print(f"\nDevice {device_id} WASAPI ({native_sr}Hz):")

    # Try different approaches
    test_playback(device_id, native_sr, 0.5, "play")
    test_playback(device_id, native_sr, 0.5, "stream")
    test_playback(device_id, native_sr, 0.5, "stream_small_buffer")

print("\n" + "=" * 60)
print("Testing with sdl2 if available...")
print("=" * 60)

try:
    # Set SDL audio driver before importing pygame
    import os
    os.environ['SDL_AUDIODRIVER'] = 'directsound'
    import pygame
    pygame.mixer.init(frequency=44100, size=-16, channels=1)

    # Generate tone as pygame Sound
    tone = generate_tone(1000, 0.5, 44100)
    tone_int16 = (tone * 32767).astype(np.int16)
    sound = pygame.sndarray.make_sound(tone_int16)

    print("\nPygame/SDL2:")
    start = time.time()
    sound.play()
    while pygame.mixer.get_busy():
        time.sleep(0.01)
    elapsed = time.time() - start
    print(f"  Playback took: {elapsed:.2f}s")
    pygame.mixer.quit()
except ImportError:
    print("  pygame not installed")
except Exception as e:
    print(f"  pygame error: {e}")

print("\n" + "=" * 60)
print("Done")
print("=" * 60)
