# Modumb: Acoustic Modem Git Transport

Git clone/fetch over audio using FSK modulation through your speaker and microphone.

## Quick Start

### Installation

```bash
cd /home/john/modumb

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the package
pip install -e .

# Check audio device status
modem-audio devices
```

### Add to PATH (for git to find the remote helper)

```bash
# Add to your ~/.bashrc or ~/.zshrc:
export PATH="/home/john/modumb/bin:$PATH"

# Or create a symlink:
sudo ln -s /home/john/modumb/bin/git-remote-modem /usr/local/bin/
```

## Platform Setup

### Windows (Native)

Works out of the box. If issues:
```cmd
pip install --force-reinstall sounddevice
```

### macOS

```bash
brew install portaudio
pip install --force-reinstall sounddevice
```

Grant microphone permission in System Preferences > Security & Privacy.

### Linux (Native)

```bash
sudo apt install libportaudio2 portaudio19-dev  # Ubuntu/Debian
sudo dnf install portaudio portaudio-devel       # Fedora
pip install --force-reinstall sounddevice
```

### WSL2 (Windows Subsystem for Linux)

WSL2 requires extra setup since it doesn't have native audio:

**Option 1: WSLg (Windows 11 - Recommended)**
```bash
# If you're on Windows 11, WSLg may already work
sudo apt install libportaudio2
modem-audio devices  # Should show PulseAudio devices
```

**Option 2: PulseAudio Forwarding (Windows 10/11)**

1. Install PulseAudio on Windows:
   ```powershell
   choco install pulseaudio
   # Or download from https://www.freedesktop.org/wiki/Software/PulseAudio/Ports/Windows/Support/
   ```

2. Edit PulseAudio config (`%APPDATA%\PulseAudio\default.pa`):
   ```
   load-module module-native-protocol-tcp auth-anonymous=1
   load-module module-waveout
   ```

3. In WSL2, add to `~/.bashrc`:
   ```bash
   export PULSE_SERVER=tcp:$(cat /etc/resolv.conf | grep nameserver | awk '{print $2}')
   ```

4. Install in WSL2:
   ```bash
   sudo apt install libportaudio2 pulseaudio-utils
   ```

**Option 3: Loopback Mode (Testing only)**
```bash
MODEM_LOOPBACK=1 git clone modem://audio/repo
```

## Usage

### List Audio Devices

```bash
modem-audio devices          # List all devices
modem-audio test             # Test default devices
modem-audio test -i 3 -o 5   # Test specific devices
```

### Server Side (machine with the repository)

```bash
source /home/john/modumb/.venv/bin/activate

# Start server with default devices
modem-git-server /path/to/your/repo

# Start server with specific devices
modem-git-server -i 3 -o 5 /path/to/your/repo
```

### Client Side (machine cloning the repository)

```bash
source /home/john/modumb/.venv/bin/activate

# Clone with default devices
git clone modem://audio/repo local-copy

# Clone with specific devices (via environment variables)
MODEM_INPUT_DEVICE=3 MODEM_OUTPUT_DEVICE=5 git clone modem://audio/repo local-copy
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `MODEM_INPUT_DEVICE` | Input device index (microphone) |
| `MODEM_OUTPUT_DEVICE` | Output device index (speaker) |
| `MODEM_LOOPBACK` | Set to `1` for loopback mode (no audio) |
| `MODEM_AUDIBLE` | Set to `1` to hear audio in loopback mode |

### Audible Loopback (Demo Mode)

Want to hear what the modem sounds like without needing two machines?

```bash
# Server with audible loopback
modem-git-server --loopback --audible /path/to/repo

# Client with audible loopback
MODEM_LOOPBACK=1 MODEM_AUDIBLE=1 git clone modem://audio/repo
```

Or test directly with Python:
```python
from modumb.modem import Modem

modem = Modem(loopback=True, audible=True)
modem.start()
modem.send(b'Hello, World!')  # You'll hear the modem sounds!
modem.stop()
```

## Testing with Loopback (No Audio Hardware)

For testing without actual speaker/microphone:

```bash
# Terminal 1: Start server with loopback
source .venv/bin/activate
modem-git-server --loopback /path/to/repo

# Terminal 2: Clone with loopback
source .venv/bin/activate
MODEM_LOOPBACK=1 git clone modem://audio/repo test-clone
```

## How It Works

```
┌─────────────┐     Audio      ┌─────────────┐
│   Client    │  ~~~~~~~~~~~~  │   Server    │
│  (git cli)  │   Speaker/Mic  │ (git repo)  │
└─────────────┘                └─────────────┘

Layer 5: Git Remote Helper ←→ Smart HTTP Protocol
Layer 4: HTTP Client/Server
Layer 3: Reliable Transport (Stop-and-Wait ARQ)
Layer 2: Data Link (Frames + CRC-16)
Layer 1: Physical (AFSK Modem: 1200/2200 Hz)
```

### Specifications

| Parameter | Value |
|-----------|-------|
| Modulation | AFSK (Audio FSK) |
| Mark frequency (1) | 1200 Hz |
| Space frequency (0) | 2200 Hz |
| Baud rate | 300 baud |
| Data rate | ~37.5 bytes/sec |
| Sample rate | 48000 Hz |
| Max frame payload | 256 bytes |
| Error detection | CRC-16-CCITT |

## Physical Setup for Real Audio

### Audio Loopback Cable
Connect line-out to line-in with a 3.5mm audio cable for testing.

### Two Computers
1. Place computers 1-2 meters apart in a quiet room
2. Point speakers toward each other's microphones
3. Set volume to ~50% to avoid distortion
4. Disable system sounds and notifications

### Troubleshooting Audio
```bash
# List audio devices
python3 -c "import sounddevice; print(sounddevice.query_devices())"

# Test audio output
python3 -c "
import sounddevice as sd
import numpy as np
t = np.linspace(0, 1, 48000)
sd.play(0.5 * np.sin(2*np.pi*1000*t), 48000)
sd.wait()
"
```

## Project Structure

```
modumb/
├── bin/
│   ├── git-remote-modem      # Git remote helper
│   └── modem-git-server      # Server entry point
├── src/modumb/
│   ├── modem/                # Physical layer (AFSK)
│   ├── datalink/             # Framing + CRC
│   ├── transport/            # Reliable delivery (ARQ)
│   ├── http/                 # HTTP + pkt-line
│   └── git/                  # Git smart HTTP
└── tests/
```

## Running Tests

```bash
source .venv/bin/activate
pytest tests/ -v
```

## Limitations (Proof of Concept)

- **Fetch only**: Push not yet implemented
- **Slow**: ~37.5 bytes/sec at 300 baud (a 10KB repo takes ~5 minutes)
- **Small repos**: Best for repos under 50KB
- **Quiet environment**: Background noise causes errors
- **Half-duplex**: Can't send and receive simultaneously

## Example Session

```bash
# Create a tiny test repo
mkdir /tmp/test-repo && cd /tmp/test-repo
git init
echo "Hello from acoustic modem!" > README.md
git add . && git commit -m "Initial commit"

# Server terminal
source /home/john/modumb/.venv/bin/activate
modem-git-server --loopback /tmp/test-repo

# Client terminal
source /home/john/modumb/.venv/bin/activate
MODEM_LOOPBACK=1 git clone modem://audio/repo /tmp/cloned-repo

# Verify
cat /tmp/cloned-repo/README.md
```
