"""AFSK (Audio Frequency Shift Keying) modulation and demodulation.

Bell 202 style AFSK:
- Mark (binary 1): 1200 Hz
- Space (binary 0): 2200 Hz
- Baud rate: 300 baud (upgradeable to 1200)
- Sample rate: 48000 Hz
"""

import numpy as np
from scipy import signal
from typing import Iterator


# AFSK parameters
SAMPLE_RATE = 48000  # Hz
MARK_FREQ = 1200     # Hz (binary 1)
SPACE_FREQ = 2200    # Hz (binary 0)
BAUD_RATE = 300      # bits per second

# Derived constants
SAMPLES_PER_BIT = SAMPLE_RATE // BAUD_RATE  # 160 samples at 300 baud


class AFSKModulator:
    """Modulate bytes into AFSK audio samples."""

    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        mark_freq: int = MARK_FREQ,
        space_freq: int = SPACE_FREQ,
        baud_rate: int = BAUD_RATE,
    ):
        self.sample_rate = sample_rate
        self.mark_freq = mark_freq
        self.space_freq = space_freq
        self.baud_rate = baud_rate
        self.samples_per_bit = sample_rate // baud_rate
        self.phase = 0.0  # Continuous phase for smooth transitions

    def modulate_bit(self, bit: int) -> np.ndarray:
        """Generate audio samples for a single bit."""
        freq = self.mark_freq if bit else self.space_freq
        t = np.arange(self.samples_per_bit) / self.sample_rate

        # Generate sine wave with continuous phase
        samples = np.sin(2 * np.pi * freq * t + self.phase)

        # Update phase for next bit (maintain continuity)
        self.phase += 2 * np.pi * freq * self.samples_per_bit / self.sample_rate
        self.phase = self.phase % (2 * np.pi)  # Wrap phase

        return samples.astype(np.float32)

    def modulate_byte(self, byte: int) -> np.ndarray:
        """Generate audio samples for a single byte (LSB first)."""
        samples = []
        for i in range(8):
            bit = (byte >> i) & 1
            samples.append(self.modulate_bit(bit))
        return np.concatenate(samples)

    def modulate(self, data: bytes) -> np.ndarray:
        """Generate audio samples for a sequence of bytes."""
        if not data:
            return np.array([], dtype=np.float32)

        samples = []
        for byte in data:
            samples.append(self.modulate_byte(byte))

        return np.concatenate(samples)

    def reset(self) -> None:
        """Reset the modulator state."""
        self.phase = 0.0


class AFSKDemodulator:
    """Demodulate AFSK audio samples into bytes."""

    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        mark_freq: int = MARK_FREQ,
        space_freq: int = SPACE_FREQ,
        baud_rate: int = BAUD_RATE,
    ):
        self.sample_rate = sample_rate
        self.mark_freq = mark_freq
        self.space_freq = space_freq
        self.baud_rate = baud_rate
        self.samples_per_bit = sample_rate // baud_rate

        # Design bandpass filters for mark and space frequencies
        # Using relatively wide bandwidth to handle frequency drift
        bandwidth = 200  # Hz

        self.mark_filter = self._design_bandpass(
            mark_freq - bandwidth/2,
            mark_freq + bandwidth/2
        )
        self.space_filter = self._design_bandpass(
            space_freq - bandwidth/2,
            space_freq + bandwidth/2
        )

        # Low-pass filter for envelope detection
        self.envelope_filter = self._design_lowpass(baud_rate * 1.5)

    def _design_bandpass(self, low_freq: float, high_freq: float) -> tuple:
        """Design a bandpass filter."""
        nyquist = self.sample_rate / 2
        low = low_freq / nyquist
        high = high_freq / nyquist

        # Clamp to valid range
        low = max(0.001, min(low, 0.999))
        high = max(low + 0.001, min(high, 0.999))

        b, a = signal.butter(4, [low, high], btype='band')
        return b, a

    def _design_lowpass(self, cutoff: float) -> tuple:
        """Design a lowpass filter."""
        nyquist = self.sample_rate / 2
        normalized = cutoff / nyquist
        normalized = max(0.001, min(normalized, 0.999))

        b, a = signal.butter(4, normalized, btype='low')
        return b, a

    def _envelope_detect(self, samples: np.ndarray, bandpass: tuple) -> np.ndarray:
        """Detect envelope of filtered signal."""
        # Bandpass filter
        filtered = signal.lfilter(bandpass[0], bandpass[1], samples)

        # Full-wave rectification
        rectified = np.abs(filtered)

        # Low-pass filter for envelope
        envelope = signal.lfilter(
            self.envelope_filter[0],
            self.envelope_filter[1],
            rectified
        )

        return envelope

    def demodulate_bit(self, samples: np.ndarray) -> int:
        """Demodulate a single bit from audio samples."""
        if len(samples) < self.samples_per_bit // 2:
            return 0

        # Get envelopes for mark and space frequencies
        mark_env = self._envelope_detect(samples, self.mark_filter)
        space_env = self._envelope_detect(samples, self.space_filter)

        # Compare average energies
        mark_energy = np.mean(mark_env)
        space_energy = np.mean(space_env)

        return 1 if mark_energy > space_energy else 0

    def demodulate(self, samples: np.ndarray) -> bytes:
        """Demodulate audio samples into bytes."""
        if len(samples) < self.samples_per_bit * 8:
            return b''

        # Process entire signal to get bit decisions
        mark_env = self._envelope_detect(samples, self.mark_filter)
        space_env = self._envelope_detect(samples, self.space_filter)

        # Determine bits at sample points
        bits = []
        num_bits = len(samples) // self.samples_per_bit

        for i in range(num_bits):
            start = i * self.samples_per_bit
            end = start + self.samples_per_bit

            # Skip filter transient at start
            if i < 2:
                continue

            mark_energy = np.mean(mark_env[start:end])
            space_energy = np.mean(space_env[start:end])

            bits.append(1 if mark_energy > space_energy else 0)

        # Convert bits to bytes (LSB first)
        result = bytearray()
        for i in range(0, len(bits) - 7, 8):
            byte = 0
            for j in range(8):
                byte |= bits[i + j] << j
            result.append(byte)

        return bytes(result)


def generate_test_tone(freq: float, duration: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Generate a test tone for debugging."""
    t = np.arange(int(duration * sample_rate)) / sample_rate
    return np.sin(2 * np.pi * freq * t).astype(np.float32)
