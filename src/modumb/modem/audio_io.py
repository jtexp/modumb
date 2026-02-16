"""Audio I/O interface using sounddevice.

Provides cross-platform audio input/output for the AFSK modem.
Supports both blocking and callback-based operation.

Cross-platform support:
- Windows: Uses WASAPI/DirectSound (works out of box)
- macOS: Uses CoreAudio (may need microphone permission)
- Linux: Uses ALSA/PulseAudio (needs libportaudio2)
- WSL2: Needs PulseAudio forwarding or WSLg

Device selection via environment variables:
- MODEM_INPUT_DEVICE: Input device index
- MODEM_OUTPUT_DEVICE: Output device index
- MODEM_LOOPBACK: Set to 1 for loopback mode (no audio hardware)
"""

import os
import threading
import queue
import time
from typing import Optional, Callable, Union
import numpy as np

try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except (ImportError, OSError) as e:
    sd = None  # type: ignore
    SOUNDDEVICE_AVAILABLE = False
    SOUNDDEVICE_ERROR = str(e)

from .afsk import SAMPLE_RATE


def get_device_from_env(var_name: str) -> Optional[int]:
    """Get device index from environment variable."""
    value = os.environ.get(var_name)
    if value is not None:
        try:
            return int(value)
        except ValueError:
            pass
    return None


def is_loopback_mode() -> bool:
    """Check if loopback mode is enabled via environment."""
    value = os.environ.get('MODEM_LOOPBACK', '').lower()
    return value in ('1', 'true', 'yes', 'on')


def is_audible_mode() -> bool:
    """Check if audible mode is enabled via environment."""
    value = os.environ.get('MODEM_AUDIBLE', '').lower()
    return value in ('1', 'true', 'yes', 'on')


class AudioInterface:
    """Cross-platform audio I/O using sounddevice."""

    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        channels: int = 1,
        blocksize: int = 1024,
        device: Optional[int] = None,
        input_device: Optional[int] = None,
        output_device: Optional[int] = None,
        loopback: bool = False,
        audible: bool = False,
    ):
        """Initialize audio interface.

        Args:
            sample_rate: Audio sample rate in Hz
            channels: Number of audio channels (1 = mono)
            blocksize: Number of samples per audio block
            device: Device index for both input and output (legacy)
            input_device: Input device index, or None for default/env
            output_device: Output device index, or None for default/env
            loopback: If True, use internal loopback (for testing)
            audible: If True, play audio even in loopback mode (demo mode)

        Environment variables:
            MODEM_INPUT_DEVICE: Override input device
            MODEM_OUTPUT_DEVICE: Override output device
            MODEM_LOOPBACK: Enable loopback mode (1/true/yes)
            MODEM_AUDIBLE: Play audio in loopback mode (1/true/yes)
        """
        self.sample_rate = sample_rate
        self.channels = channels
        self.blocksize = blocksize

        # Device selection priority: argument > environment > default
        self.input_device = (
            input_device
            or device
            or get_device_from_env('MODEM_INPUT_DEVICE')
        )
        self.output_device = (
            output_device
            or device
            or get_device_from_env('MODEM_OUTPUT_DEVICE')
        )

        # Loopback mode: argument or environment
        self.loopback = loopback or is_loopback_mode()

        # Audible mode: play audio even in loopback (for demos)
        self.audible = audible or is_audible_mode()

        # Queues for async I/O
        self._tx_queue: queue.Queue[Optional[np.ndarray]] = queue.Queue()
        self._rx_queue: queue.Queue[np.ndarray] = queue.Queue()

        # Loopback buffer for testing
        self._loopback_buffer: queue.Queue[np.ndarray] = queue.Queue()

        # Stream state
        self._input_stream: Optional["sd.InputStream"] = None
        self._output_stream: Optional["sd.OutputStream"] = None
        self._running = False
        self._lock = threading.Lock()

        # Echo suppression state
        self._transmitting = False
        self._last_tx_end: float = 0.0
        self._echo_guard_time: float = 0.08  # Time to wait after TX for echo to die

    def start(self) -> None:
        """Start audio streams."""
        if self.loopback:
            # Loopback mode - no streams needed (audible uses sd.play directly)
            if self.audible and not SOUNDDEVICE_AVAILABLE:
                print("Warning: Audio not available, using silent loopback")
            self._running = True
            return

        if not SOUNDDEVICE_AVAILABLE:
            raise RuntimeError(f"sounddevice not available: {SOUNDDEVICE_ERROR}")

        with self._lock:
            if self._running:
                return

            # Create input stream for receiving
            # (transmit uses sd.play directly for better compatibility)
            self._input_stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                blocksize=self.blocksize,
                device=self.input_device,
                dtype=np.float32,
                callback=self._input_callback,
            )

            self._input_stream.start()
            self._running = True

    def stop(self) -> None:
        """Stop audio streams."""
        with self._lock:
            self._running = False

            if self._input_stream:
                self._input_stream.stop()
                self._input_stream.close()
                self._input_stream = None

            # Note: output uses sd.play() directly, no persistent stream
            self._output_stream = None

    def _input_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: dict,
        status: "sd.CallbackFlags",
    ) -> None:
        """Callback for input stream - receive audio samples."""
        if status:
            pass  # Could log status flags

        # Echo suppression: ignore audio during transmission and guard period
        if self._transmitting:
            return
        if time.time() < self._last_tx_end + self._echo_guard_time:
            return

        # Copy data to queue
        self._rx_queue.put(indata.copy().flatten())

    def clear_receive_buffer(self) -> None:
        """Clear the receive buffer (discard any pending audio)."""
        while True:
            try:
                self._rx_queue.get_nowait()
            except queue.Empty:
                break

    def _output_callback(
        self,
        outdata: np.ndarray,
        frames: int,
        time_info: dict,
        status: "sd.CallbackFlags",
    ) -> None:
        """Callback for output stream - send audio samples."""
        try:
            data = self._tx_queue.get_nowait()
            if data is not None and len(data) >= frames:
                outdata[:, 0] = data[:frames]
                # Put remainder back
                if len(data) > frames:
                    remainder = data[frames:]
                    # Create new queue with remainder at front
                    self._tx_queue.put(remainder)
            else:
                outdata.fill(0)
        except queue.Empty:
            outdata.fill(0)

    def transmit(self, samples: np.ndarray, blocking: bool = True) -> None:
        """Transmit audio samples.

        Args:
            samples: Audio samples to transmit (float32, -1 to 1)
            blocking: If True, wait until transmission complete
        """
        if not self._running:
            raise RuntimeError("Audio interface not running")

        samples_f32 = samples.astype(np.float32)

        if self.loopback:
            # In loopback mode, put directly in receive buffer
            self._loopback_buffer.put(samples_f32.copy())

            if self.audible and SOUNDDEVICE_AVAILABLE:
                # Play through speakers for demo using direct playback
                sd.play(samples_f32, self.sample_rate, device=self.output_device)
                if blocking:
                    sd.wait()
            return

        # Echo suppression: mark as transmitting, clear buffer
        self._transmitting = True
        self.clear_receive_buffer()

        # Use direct playback for reliable transmission
        # (callback-based output doesn't work well with some audio backends)
        sd.play(samples_f32, self.sample_rate, device=self.output_device)
        if blocking:
            sd.wait()

        # Echo suppression: record end time, clear any echo that snuck through
        self._transmitting = False
        self._last_tx_end = time.time()
        self.clear_receive_buffer()

    def receive(self, num_samples: int, timeout: float = 5.0) -> np.ndarray:
        """Receive audio samples.

        Args:
            num_samples: Number of samples to receive
            timeout: Maximum time to wait in seconds

        Returns:
            Audio samples as float32 array
        """
        if not self._running:
            raise RuntimeError("Audio interface not running")

        if self.loopback:
            # In loopback mode, get from loopback buffer
            try:
                return self._loopback_buffer.get(timeout=timeout)
            except queue.Empty:
                return np.zeros(num_samples, dtype=np.float32)

        # Collect samples from receive queue
        samples = []
        collected = 0
        deadline = time.time() + timeout

        while collected < num_samples and time.time() < deadline:
            try:
                remaining_time = deadline - time.time()
                if remaining_time <= 0:
                    break
                block = self._rx_queue.get(timeout=min(0.1, remaining_time))
                samples.append(block)
                collected += len(block)
            except queue.Empty:
                continue

        if not samples:
            return np.zeros(num_samples, dtype=np.float32)

        result = np.concatenate(samples)
        return result[:num_samples] if len(result) >= num_samples else result

    def receive_until_silence(
        self,
        threshold: float = 0.01,
        min_samples: int = 1000,
        silence_duration: float = 0.2,
        timeout: float = 10.0,
    ) -> np.ndarray:
        """Receive audio until silence is detected.

        Args:
            threshold: RMS threshold for silence detection
            min_samples: Minimum samples to collect before checking silence
            silence_duration: Duration of silence to trigger stop
            timeout: Maximum time to wait

        Returns:
            Audio samples as float32 array
        """
        samples = []
        collected = 0
        silence_samples = int(silence_duration * self.sample_rate)
        deadline = time.time() + timeout
        signal_detected = False

        while time.time() < deadline:
            block = self.receive(self.blocksize, timeout=0.1)
            if len(block) == 0:
                continue

            samples.append(block)
            collected += len(block)

            # Check for signal presence (RMS above threshold)
            block_rms = np.sqrt(np.mean(block ** 2))
            if block_rms > threshold * 2:  # Signal is present
                signal_detected = True

            # Only check for silence AFTER we've detected signal
            if signal_detected and collected >= min_samples:
                # Need enough samples to check for silence_duration
                # Each block is blocksize (1024) samples, so we need at least
                # silence_samples / blocksize blocks
                blocks_needed = max(10, (silence_samples // self.blocksize) + 1)
                recent = np.concatenate(samples[-blocks_needed:]) if len(samples) >= blocks_needed else np.concatenate(samples)
                # Only check if we have enough samples for the silence duration
                if len(recent) >= silence_samples:
                    rms = np.sqrt(np.mean(recent[-silence_samples:] ** 2))
                    if rms < threshold:
                        break

        return np.concatenate(samples) if samples else np.zeros(0, dtype=np.float32)

    @property
    def is_running(self) -> bool:
        """Check if audio interface is running."""
        return self._running

    def __enter__(self) -> "AudioInterface":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


class LoopbackAudioInterface(AudioInterface):
    """Audio interface with internal loopback for testing."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, **kwargs):
        super().__init__(sample_rate=sample_rate, loopback=True, **kwargs)


def list_audio_devices() -> list[dict]:
    """List available audio devices."""
    if sd is None:
        return []

    devices = sd.query_devices()
    result = []

    for i, dev in enumerate(devices):
        result.append({
            "index": i,
            "name": dev["name"],
            "channels_in": dev["max_input_channels"],
            "channels_out": dev["max_output_channels"],
            "sample_rate": dev["default_samplerate"],
        })

    return result
