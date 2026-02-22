#!/usr/bin/env python3
import sounddevice as sd

print("DELL monitor audio devices:")
print("-" * 70)
for i, d in enumerate(sd.query_devices()):
    if "DELL" in d["name"] and d["max_output_channels"] > 0:
        print(f"  {i}: {d['name']}")
        print(f"      hostapi: {d['hostapi']}, sample_rate: {d['default_samplerate']}")
        print(f"      latency: {d['default_low_output_latency']:.3f}s")
        print()

print("Host APIs:")
for i, api in enumerate(sd.query_hostapis()):
    print(f"  {i}: {api['name']}")
