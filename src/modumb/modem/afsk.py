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
        # Using wider bandwidth to handle frequency drift and timing variations
        bandwidth = 400  # Hz (increased to handle more variation)

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
        # Use filtfilt for zero-phase filtering (no delay)
        try:
            # Pad signal to avoid edge effects
            pad_len = 3 * max(len(bandpass[0]), len(bandpass[1]))
            if len(samples) > pad_len:
                filtered = signal.filtfilt(bandpass[0], bandpass[1], samples)
            else:
                filtered = signal.lfilter(bandpass[0], bandpass[1], samples)
        except ValueError:
            filtered = signal.lfilter(bandpass[0], bandpass[1], samples)

        # Full-wave rectification
        rectified = np.abs(filtered)

        # Low-pass filter for envelope (also zero-phase)
        try:
            if len(rectified) > pad_len:
                envelope = signal.filtfilt(
                    self.envelope_filter[0],
                    self.envelope_filter[1],
                    rectified
                )
            else:
                envelope = signal.lfilter(
                    self.envelope_filter[0],
                    self.envelope_filter[1],
                    rectified
                )
        except ValueError:
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

    def find_signal_start(self, samples: np.ndarray, threshold_ratio: float = 0.3) -> int:
        """Find where the signal starts in the samples.

        Uses amplitude detection to find signal, then looks for
        the preamble pattern (0xAA = alternating bits) to align on bit boundaries.

        Args:
            samples: Audio samples
            threshold_ratio: Ratio of max amplitude to use as threshold

        Returns:
            Sample index where signal starts (aligned to bit boundary)
        """
        if len(samples) == 0:
            return 0

        # Find where amplitude exceeds threshold
        max_amp = np.max(np.abs(samples))
        if max_amp < 0.01:  # No signal
            return 0

        threshold = max_amp * threshold_ratio
        above_threshold = np.where(np.abs(samples) > threshold)[0]

        if len(above_threshold) == 0:
            return 0

        # Start searching a bit before the first threshold crossing
        search_start = max(0, above_threshold[0] - self.samples_per_bit * 2)

        return search_start

    def demodulate(self, samples: np.ndarray, auto_sync: bool = True) -> bytes:
        """Demodulate audio samples into bytes.

        Args:
            samples: Audio samples to demodulate
            auto_sync: If True, automatically find signal start and bit alignment

        Returns:
            Demodulated bytes
        """
        if len(samples) < self.samples_per_bit * 8:
            return b''

        # Auto-detect signal start
        if auto_sync:
            base_offset = self.find_signal_start(samples)

            # If signal is weak or no clear start, just demodulate from beginning
            if base_offset <= 0:
                return self._demodulate_raw(samples)

            # Try different bit-phase offsets to find best alignment
            # Search range: -2 to +3 bit periods around detected start
            # Look for preamble pattern (0xAA)
            best_result = b''
            best_score = -1
            first_result = None
            step = max(1, self.samples_per_bit // 16)

            search_start = max(0, base_offset - self.samples_per_bit * 2)
            search_end = min(len(samples) - self.samples_per_bit * 8,
                           base_offset + self.samples_per_bit * 3)

            for offset in range(search_start, search_end, step):
                result = self._demodulate_raw(samples[offset:])
                if len(result) < 1:
                    continue
                if first_result is None:
                    first_result = result
                # Score by counting preamble bytes (0xAA) in first 16 bytes
                # Also check for SYNC pattern (0x7E) after preamble
                preamble_score = sum(1 for b in result[:16] if b == 0xAA)
                # Bonus points if we find SYNC (0x7E) after preamble-like bytes
                sync_bonus = 0
                for i in range(8, min(20, len(result) - 1)):
                    if result[i] == 0x7E and result[i+1] == 0x7E:
                        sync_bonus = 4  # Strong bonus for finding SYNC
                        break
                score = preamble_score + sync_bonus
                if score > best_score:
                    best_score = score
                    best_result = result
                    if preamble_score >= 14 and sync_bonus > 0:  # Near-perfect
                        break

            # If no preamble found, return first result (fallback)
            if best_score <= 0 and first_result is not None:
                return first_result

            return best_result
        else:
            return self._demodulate_raw(samples)

    def _demodulate_raw(self, samples: np.ndarray) -> bytes:
        """Demodulate without sync detection."""
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
