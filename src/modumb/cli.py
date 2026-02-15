"""Command-line utilities for modumb.

Cross-platform audio device management for Windows, macOS, Linux, and WSL2.
"""

import argparse
import sys
import os
from typing import Optional

# Check for sounddevice availability
try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except (ImportError, OSError) as e:
    SOUNDDEVICE_AVAILABLE = False
    SOUNDDEVICE_ERROR = str(e)


def get_platform_info() -> dict:
    """Get platform information for audio support."""
    import platform

    info = {
        'system': platform.system(),
        'release': platform.release(),
        'is_wsl': False,
        'is_wsl2': False,
        'audio_available': SOUNDDEVICE_AVAILABLE,
        'audio_error': None if SOUNDDEVICE_AVAILABLE else SOUNDDEVICE_ERROR,
    }

    # Detect WSL
    if info['system'] == 'Linux':
        try:
            with open('/proc/version', 'r') as f:
                version = f.read().lower()
                if 'microsoft' in version or 'wsl' in version:
                    info['is_wsl'] = True
                    if 'wsl2' in version:
                        info['is_wsl2'] = True
        except FileNotFoundError:
            pass

    return info


def list_devices() -> list[dict]:
    """List available audio devices.

    Returns:
        List of device info dicts with keys: index, name, inputs, outputs, default_in, default_out
    """
    if not SOUNDDEVICE_AVAILABLE:
        return []

    devices = sd.query_devices()
    default_in = sd.default.device[0]
    default_out = sd.default.device[1]

    result = []
    for i, dev in enumerate(devices):
        result.append({
            'index': i,
            'name': dev['name'],
            'inputs': dev['max_input_channels'],
            'outputs': dev['max_output_channels'],
            'sample_rate': int(dev['default_samplerate']),
            'default_in': i == default_in,
            'default_out': i == default_out,
        })

    return result


def print_devices():
    """Print audio devices in a formatted table."""
    platform_info = get_platform_info()

    print(f"Platform: {platform_info['system']} {platform_info['release']}")
    if platform_info['is_wsl']:
        print(f"WSL detected: {'WSL2' if platform_info['is_wsl2'] else 'WSL1'}")
    print()

    if not platform_info['audio_available']:
        print(f"Audio ERROR: {platform_info['audio_error']}")
        print()
        print_audio_setup_help(platform_info)
        return

    devices = list_devices()

    if not devices:
        print("No audio devices found!")
        print()
        print_audio_setup_help(platform_info)
        return

    # Print input devices
    print("INPUT DEVICES (microphones):")
    print("-" * 60)
    inputs = [d for d in devices if d['inputs'] > 0]
    if inputs:
        for dev in inputs:
            default = " [DEFAULT]" if dev['default_in'] else ""
            print(f"  {dev['index']:3d}: {dev['name'][:45]:<45} {dev['inputs']}ch{default}")
    else:
        print("  (none)")
    print()

    # Print output devices
    print("OUTPUT DEVICES (speakers):")
    print("-" * 60)
    outputs = [d for d in devices if d['outputs'] > 0]
    if outputs:
        for dev in outputs:
            default = " [DEFAULT]" if dev['default_out'] else ""
            print(f"  {dev['index']:3d}: {dev['name'][:45]:<45} {dev['outputs']}ch{default}")
    else:
        print("  (none)")
    print()

    print("Usage:")
    print("  modem-git-server --input-device 3 --output-device 5 /path/to/repo")
    print("  MODEM_INPUT_DEVICE=3 MODEM_OUTPUT_DEVICE=5 git clone modem://audio/repo")


def print_audio_setup_help(platform_info: dict):
    """Print platform-specific audio setup instructions."""

    if platform_info['is_wsl2']:
        print("""
WSL2 AUDIO SETUP:
================
WSL2 doesn't have native audio. You have two options:

Option 1: WSLg (Windows 11, easiest)
  - Update to Windows 11 with WSLg support
  - Audio should work automatically via PulseAudio

Option 2: PulseAudio forwarding (Windows 10/11)
  1. Install PulseAudio on Windows:
     - Download from: https://www.freedesktop.org/wiki/Software/PulseAudio/Ports/Windows/Support/
     - Or use: choco install pulseaudio

  2. Configure PulseAudio on Windows (edit config/default.pa):
     load-module module-native-protocol-tcp auth-anonymous=1
     load-module module-waveout

  3. In WSL2, add to ~/.bashrc:
     export PULSE_SERVER=tcp:$(cat /etc/resolv.conf | grep nameserver | awk '{print $2}')

  4. Install PulseAudio in WSL2:
     sudo apt install libportaudio2 pulseaudio-utils

  5. Restart WSL2 and try again

Option 3: Use loopback mode for testing
  MODEM_LOOPBACK=1 git clone modem://audio/repo
""")

    elif platform_info['is_wsl']:
        print("""
WSL1 AUDIO SETUP:
================
WSL1 has limited audio support. Consider upgrading to WSL2 with WSLg,
or use loopback mode for testing:
  MODEM_LOOPBACK=1 git clone modem://audio/repo
""")

    elif platform_info['system'] == 'Linux':
        print("""
LINUX AUDIO SETUP:
=================
Install PortAudio development libraries:

  Ubuntu/Debian:
    sudo apt install libportaudio2 portaudio19-dev

  Fedora:
    sudo dnf install portaudio portaudio-devel

  Arch:
    sudo pacman -S portaudio

Then reinstall sounddevice:
    pip install --force-reinstall sounddevice
""")

    elif platform_info['system'] == 'Darwin':
        print("""
MACOS AUDIO SETUP:
=================
Install PortAudio:
    brew install portaudio

Then reinstall sounddevice:
    pip install --force-reinstall sounddevice

Check System Preferences > Security & Privacy > Microphone
to ensure terminal has microphone access.
""")

    elif platform_info['system'] == 'Windows':
        print("""
WINDOWS AUDIO SETUP:
===================
sounddevice should work out of the box on Windows.

If you see errors:
1. Install Visual C++ Redistributable:
   https://aka.ms/vs/17/release/vc_redist.x64.exe

2. Reinstall sounddevice:
   pip install --force-reinstall sounddevice

3. Check Windows Sound settings for default devices
""")


def test_audio(input_device: Optional[int] = None, output_device: Optional[int] = None):
    """Test audio input and output."""
    if not SOUNDDEVICE_AVAILABLE:
        print(f"Audio not available: {SOUNDDEVICE_ERROR}")
        return False

    import numpy as np

    print("Testing audio devices...")
    print()

    # Test output
    try:
        print(f"Testing OUTPUT (device {output_device or 'default'})...")
        print("  Playing 1000 Hz tone for 1 second...")

        sample_rate = 48000
        duration = 1.0
        t = np.linspace(0, duration, int(sample_rate * duration), dtype=np.float32)
        tone = 0.3 * np.sin(2 * np.pi * 1000 * t)

        sd.play(tone, sample_rate, device=output_device)
        sd.wait()
        print("  Output: OK")
    except Exception as e:
        print(f"  Output FAILED: {e}")
        return False

    print()

    # Test input
    try:
        print(f"Testing INPUT (device {input_device or 'default'})...")
        print("  Recording 1 second of audio...")

        recording = sd.rec(int(sample_rate * 1), samplerate=sample_rate,
                          channels=1, device=input_device, dtype=np.float32)
        sd.wait()

        rms = np.sqrt(np.mean(recording ** 2))
        peak = np.max(np.abs(recording))
        print(f"  Input: OK (RMS: {rms:.4f}, Peak: {peak:.4f})")

        if rms < 0.001:
            print("  Warning: Very low input level - check microphone")
    except Exception as e:
        print(f"  Input FAILED: {e}")
        return False

    print()
    print("Audio test completed successfully!")
    return True


def main():
    """Main entry point for modem-audio-devices command."""
    parser = argparse.ArgumentParser(
        description='Modumb audio device management',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  modem-audio devices          List all audio devices
  modem-audio test             Test default audio devices
  modem-audio test -i 3 -o 5   Test specific devices
  modem-audio info             Show platform info
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Command')

    # devices command
    devices_parser = subparsers.add_parser('devices', help='List audio devices')

    # test command
    test_parser = subparsers.add_parser('test', help='Test audio devices')
    test_parser.add_argument('-i', '--input-device', type=int,
                            help='Input device index')
    test_parser.add_argument('-o', '--output-device', type=int,
                            help='Output device index')

    # info command
    info_parser = subparsers.add_parser('info', help='Show platform info')

    args = parser.parse_args()

    if args.command == 'devices' or args.command is None:
        print_devices()
    elif args.command == 'test':
        test_audio(args.input_device, args.output_device)
    elif args.command == 'info':
        info = get_platform_info()
        for key, value in info.items():
            print(f"{key}: {value}")


if __name__ == '__main__':
    main()
