"""High-level modem interface.

Combines AFSK modulation/demodulation with audio I/O
to provide a simple byte-oriented interface.
"""

import threading
import time
from typing import Optional, Callable
import numpy as np

from .afsk import AFSKModulator, AFSKDemodulator, SAMPLE_RATE, BAUD_RATE, SAMPLES_PER_BIT
from .audio_io import AudioInterface, LoopbackAudioInterface


# Timing constants
TURNAROUND_DELAY = 0.05  # 50ms delay for half-duplex turnaround


class Modem:
    """High-level modem interface for sending and receiving bytes."""

    def __init__(
        self,
        audio: Optional[AudioInterface] = None,
        sample_rate: int = SAMPLE_RATE,
        baud_rate: int = BAUD_RATE,
        loopback: bool = False,
        audible: bool = False,
        input_device: Optional[int] = None,
        output_device: Optional[int] = None,
    ):
        """Initialize modem.

        Args:
            audio: Audio interface to use, or None to create one
            sample_rate: Audio sample rate in Hz
            baud_rate: Data rate in bits per second
            loopback: If True, use loopback audio for testing
            audible: If True, play audio even in loopback mode (demo)
            input_device: Input device index (microphone)
            output_device: Output device index (speaker)

        Environment variables (if devices not specified):
            MODEM_INPUT_DEVICE: Input device index
            MODEM_OUTPUT_DEVICE: Output device index
            MODEM_LOOPBACK: Enable loopback mode (1/true/yes)
            MODEM_AUDIBLE: Play audio in loopback mode (1/true/yes)
        """
        self.sample_rate = sample_rate
        self.baud_rate = baud_rate

        # Create audio interface if not provided
        if audio is None:
            audio = AudioInterface(
                sample_rate=sample_rate,
                loopback=loopback,
                audible=audible,
                input_device=input_device,
                output_device=output_device,
            )
        self.audio = audio

        # Create modulator and demodulator
        self.modulator = AFSKModulator(
            sample_rate=sample_rate,
            baud_rate=baud_rate,
        )
        self.demodulator = AFSKDemodulator(
            sample_rate=sample_rate,
            baud_rate=baud_rate,
        )

        # State
        self._lock = threading.Lock()
        self._rx_callback: Optional[Callable[[bytes], None]] = None

    def start(self) -> None:
        """Start the modem."""
        self.audio.start()

    def stop(self) -> None:
        """Stop the modem."""
        self.audio.stop()

    def send(self, data: bytes, blocking: bool = True) -> None:
        """Send data over the modem.

        Args:
            data: Bytes to send
            blocking: If True, wait until transmission complete
        """
        with self._lock:
            # Modulate data to audio samples
            samples = self.modulator.modulate(data)

            # Add leading/trailing silence for audio system stabilization
            # Longer leading silence helps with filter settling and sync
            lead_silence = np.zeros(int(0.15 * self.sample_rate), dtype=np.float32)
            trail_silence = np.zeros(int(0.05 * self.sample_rate), dtype=np.float32)
            samples = np.concatenate([lead_silence, samples, trail_silence])

            # Transmit
            self.audio.transmit(samples, blocking=blocking)

            if blocking:
                # Half-duplex turnaround delay
                time.sleep(TURNAROUND_DELAY)

    def receive(self, timeout: float = 5.0) -> bytes:
        """Receive data from the modem.

        Args:
            timeout: Maximum time to wait for data

        Returns:
            Received bytes, or empty bytes on timeout
        """
        with self._lock:
            # Receive audio samples
            # Use larger min_samples to ensure we capture the full transmission
            # At 300 baud, 8 bytes preamble = 64 bits = 10240 samples
            # Add margin for startup delay and filter settling
            samples = self.audio.receive_until_silence(
                timeout=timeout,
                min_samples=10000,  # ~200ms of audio minimum
                silence_duration=0.3,  # Shorter to respond faster
            )

            if len(samples) == 0:
                return b''

            # Demodulate to bytes
            data = self.demodulator.demodulate(samples)

            return data

    def receive_bytes(self, num_bytes: int, timeout: float = 5.0) -> bytes:
        """Receive a specific number of bytes.

        Args:
            num_bytes: Number of bytes to receive
            timeout: Maximum time to wait

        Returns:
            Received bytes (may be less than requested on timeout)
        """
        # Calculate expected number of samples
        bits_needed = num_bytes * 8
        samples_needed = bits_needed * (self.sample_rate // self.baud_rate)
        # Add margin for preamble detection
        samples_needed = int(samples_needed * 1.5)

        with self._lock:
            samples = self.audio.receive(samples_needed, timeout=timeout)

            if len(samples) == 0:
                return b''

            data = self.demodulator.demodulate(samples)
            return data[:num_bytes]

    def set_receive_callback(self, callback: Optional[Callable[[bytes], None]]) -> None:
        """Set callback for received data.

        Args:
            callback: Function to call with received data, or None to disable
        """
        self._rx_callback = callback

    @property
    def bytes_per_second(self) -> float:
        """Return the data rate in bytes per second."""
        return self.baud_rate / 8

    @property
    def is_running(self) -> bool:
        """Check if modem is running."""
        return self.audio.is_running

    def __enter__(self) -> "Modem":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


class LoopbackModem(Modem):
    """Modem with internal loopback for testing."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, baud_rate: int = BAUD_RATE):
        super().__init__(sample_rate=sample_rate, baud_rate=baud_rate, loopback=True)
