# WSL2 Audio Setup Guide

This guide covers setting up audio for Modumb on Windows Subsystem for Linux 2 (WSL2).

WSL2 doesn't have native audio hardware access, but there are several ways to get audio working for acoustic modem testing.

---

## Table of Contents

- [Option 1: WSLg (Recommended for Windows 11)](#option-1-wslg-recommended-for-windows-11)
- [Option 2: PulseAudio Virtual Loopback](#option-2-pulseaudio-virtual-loopback)
- [Option 3: Software Loopback Mode](#option-3-software-loopback-mode)
- [Troubleshooting](#troubleshooting)

---

## Option 1: WSLg (Recommended for Windows 11)

Windows 11's WSLg provides built-in audio support via PulseAudio.

### Check if WSLg is Available

```bash
# Check for WSLg PulseAudio socket
ls -la /mnt/wslg/PulseServer

# Check PulseAudio connection
pactl info | grep "Server String"
# Should show: Server String: unix:/mnt/wslg/PulseServer
```

### Install Dependencies

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install -y \
    libportaudio2 \
    portaudio19-dev \
    pulseaudio-utils \
    python3-pip \
    python3-venv

# Install modumb
cd /path/to/modumb
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Test Audio

```bash
# List audio devices
modem-audio devices

# Test audio output (should hear a tone)
modem-audio test
```

---

## Option 2: PulseAudio Virtual Loopback

For testing the acoustic modem without actual speakers/microphones, create a PulseAudio virtual loopback device. This routes audio from the virtual speaker directly to a virtual microphone.

### Prerequisites

```bash
sudo apt update
sudo apt install -y \
    pulseaudio \
    pulseaudio-utils \
    libportaudio2 \
    portaudio19-dev
```

### Create the Virtual Loopback Device

```bash
# Create a null sink (virtual speaker) that captures audio
pactl load-module module-null-sink \
    sink_name=ModemLoopback \
    sink_properties=device.description=ModemLoopback \
    rate=48000 \
    channels=1

# The null sink automatically creates a .monitor source
# This acts as a virtual microphone that receives the sink's audio

# Set as default devices
pactl set-default-sink ModemLoopback
pactl set-default-source ModemLoopback.monitor
```

### Verify Setup

```bash
# List sinks (should show ModemLoopback)
pactl list short sinks

# List sources (should show ModemLoopback.monitor)
pactl list short sources

# Check defaults
pactl info | grep -E "Default Sink|Default Source"
```

Expected output:
```
Default Sink: ModemLoopback
Default Source: ModemLoopback.monitor
```

### Make Persistent (Optional)

To automatically create the loopback device on PulseAudio start, add to `~/.config/pulse/default.pa`:

```bash
mkdir -p ~/.config/pulse

cat >> ~/.config/pulse/default.pa << 'EOF'
# Modumb virtual loopback device
.ifexists module-null-sink.so
load-module module-null-sink sink_name=ModemLoopback sink_properties=device.description=ModemLoopback rate=48000 channels=1
set-default-sink ModemLoopback
set-default-source ModemLoopback.monitor
.endif
EOF
```

### Quick Setup Script

Save this as `setup-pulseaudio-loopback.sh`:

```bash
#!/bin/bash
# setup-pulseaudio-loopback.sh - Create PulseAudio virtual loopback for Modumb

set -e

echo "=== Setting up PulseAudio Virtual Loopback ==="

# Remove existing ModemLoopback if present
EXISTING=$(pactl list short modules | grep -E "module-null-sink.*ModemLoopback" | cut -f1)
if [ -n "$EXISTING" ]; then
    echo "Removing existing ModemLoopback module ($EXISTING)..."
    pactl unload-module "$EXISTING" 2>/dev/null || true
fi

# Create new null sink
echo "Creating ModemLoopback null sink..."
MODULE_ID=$(pactl load-module module-null-sink \
    sink_name=ModemLoopback \
    sink_properties=device.description=ModemLoopback \
    rate=48000 \
    channels=1)

echo "Module loaded with ID: $MODULE_ID"

# Set as default
echo "Setting as default sink/source..."
pactl set-default-sink ModemLoopback
pactl set-default-source ModemLoopback.monitor

# Verify
echo ""
echo "=== Configuration Complete ==="
echo ""
echo "Sinks:"
pactl list short sinks | grep -E "^[0-9]"
echo ""
echo "Sources:"
pactl list short sources | grep -E "^[0-9]"
echo ""
echo "Defaults:"
pactl info | grep -E "Default Sink|Default Source"
echo ""
echo "Ready for acoustic modem testing!"
```

---

## Option 3: Software Loopback Mode

If audio setup is too complex, use the built-in software loopback mode which bypasses audio entirely.

```bash
# Server terminal
modem-git-server --loopback /path/to/repo

# Client terminal
MODEM_LOOPBACK=1 git clone modem://audio/repo local-copy
```

This mode is useful for:
- Testing without audio hardware
- CI/CD pipelines
- Quick functional verification

---

## Running the End-to-End Test

Once audio is configured, run the full test:

```bash
cd /path/to/modumb
source .venv/bin/activate
export PATH="$PWD/bin:$PATH"

# Run end-to-end test
./scripts/run-loopback-test.sh
```

### Manual Test

**Terminal 1 - Server:**
```bash
source .venv/bin/activate
modem-git-server /tmp/test-repo
```

**Terminal 2 - Client:**
```bash
source .venv/bin/activate
export PATH="$PWD/bin:$PATH"

# Create test repo first
./scripts/setup-test-repo.sh /tmp/test-repo

# Clone via acoustic modem
git clone modem://audio/repo /tmp/cloned-repo

# Verify
cat /tmp/cloned-repo/README.md
```

---

## Troubleshooting

### "Connection refused" to PulseAudio

```bash
# Check if PulseAudio is running
pulseaudio --check
echo $?  # 0 = running, non-zero = not running

# Start PulseAudio if needed
pulseaudio --start

# For WSLg, check the socket exists
ls -la /mnt/wslg/PulseServer
```

### "No devices found" in modem-audio

```bash
# Check sounddevice can find devices
python3 -c "import sounddevice; print(sounddevice.query_devices())"

# Check PulseAudio sources/sinks
pactl list short sinks
pactl list short sources

# Reinstall sounddevice
pip install --force-reinstall sounddevice
```

### ALSA Errors in WSL2

```
ALSA lib confmisc.c:855:(parse_card) cannot find card '0'
```

This is normal in WSL2 - ALSA doesn't have direct hardware access. PulseAudio handles audio routing. These messages can be ignored if PulseAudio is working.

### Buffer Underrun Errors

```
ALSA lib pcm.c:8740:(snd_pcm_recover) underrun occurred
```

These are handled by the ARQ retransmission protocol. Frames corrupted by underruns are automatically retransmitted.

### Audio Not Looping Back

If the virtual loopback isn't working:

```bash
# Check the module is loaded
pactl list short modules | grep null-sink

# Check the source is available
pactl list short sources | grep monitor

# Try recreating with specific format
pactl unload-module module-null-sink
pactl load-module module-null-sink \
    sink_name=ModemLoopback \
    format=s16le \
    rate=48000 \
    channels=1
```

### Environment Variables for Device Selection

If you have multiple audio devices:

```bash
# List devices with indices
modem-audio devices

# Select specific devices
export MODEM_INPUT_DEVICE=3   # Microphone/source index
export MODEM_OUTPUT_DEVICE=5  # Speaker/sink index

# Or pass to server
modem-git-server -i 3 -o 5 /path/to/repo
```

---

## PulseAudio Commands Reference

| Command | Description |
|---------|-------------|
| `pactl info` | Show PulseAudio server info |
| `pactl list short sinks` | List output devices |
| `pactl list short sources` | List input devices |
| `pactl list short modules` | List loaded modules |
| `pactl set-default-sink NAME` | Set default output |
| `pactl set-default-source NAME` | Set default input |
| `pactl load-module MODULE ARGS` | Load a module |
| `pactl unload-module ID` | Unload a module |
| `paplay FILE` | Play audio file |
| `parec FILE` | Record to file |

---

## See Also

- [README.md](../README.md) - Main documentation
- [DEBUGGING_JOURNEY.md](../DEBUGGING_JOURNEY.md) - Technical debugging notes
- [ISSUES.md](../ISSUES.md) - Known issues
