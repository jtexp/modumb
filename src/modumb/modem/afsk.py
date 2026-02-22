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
        # Wider bandwidth (800 Hz) for faster bit transition response
        # over real acoustic channels. Mark (800-1600) and Space (1800-2600)
        # don't overlap, so discrimination is still good.
        bandwidth = 800  # Hz

        self.mark_filter = self._design_bandpass(
            mark_freq - bandwidth/2,
            mark_freq + bandwidth/2
        )
        self.space_filter = self._design_bandpass(
            space_freq - bandwidth/2,
            space_freq + bandwidth/2
        )

        # Low-pass filter for envelope detection
        self.envelope_filter = self._design_lowpass(baud_rate * 2.0)

    def _design_bandpass(self, low_freq: float, high_freq: float) -> tuple:
        """Design a bandpass filter."""
        nyquist = self.sample_rate / 2
        low = low_freq / nyquist
        high = high_freq / nyquist

        # Clamp to valid range
        low = max(0.001, min(low, 0.999))
        high = max(low + 0.001, min(high, 0.999))

        # 2nd-order for faster response to bit transitions
        b, a = signal.butter(2, [low, high], btype='band')
        return b, a

    def _design_lowpass(self, cutoff: float) -> tuple:
        """Design a lowpass filter."""
        nyquist = self.sample_rate / 2
        normalized = cutoff / nyquist
        normalized = max(0.001, min(normalized, 0.999))

        b, a = signal.butter(2, normalized, btype='low')
        return b, a

    def _envelope_detect(self, samples: np.ndarray, bandpass: tuple) -> np.ndarray:
        """Detect envelope of filtered signal using single-pass filter."""
        filtered = signal.lfilter(bandpass[0], bandpass[1], samples)
        rectified = np.abs(filtered)
        envelope = signal.lfilter(
            self.envelope_filter[0],
            self.envelope_filter[1],
            rectified
        )
        return envelope

    def _goertzel_magnitude(self, samples: np.ndarray, freq: float) -> float:
        """Compute signal magnitude at a specific frequency using Goertzel algorithm."""
        N = len(samples)
        k = round(N * freq / self.sample_rate)
        w = 2 * np.pi * k / N
        coeff = 2 * np.cos(w)
        s1, s2 = 0.0, 0.0
        for x in samples:
            s0 = x + coeff * s1 - s2
            s2 = s1
            s1 = s0
        return np.sqrt(s1 * s1 + s2 * s2 - coeff * s1 * s2)

    def _dft_magnitudes(self, samples: np.ndarray, offset: int) -> tuple[np.ndarray, np.ndarray]:
        """Compute DFT magnitudes at mark and space frequencies for all bit periods.

        Uses vectorized numpy operations for speed. Each bit is evaluated
        independently - no filter memory between bits.
        """
        num_bits = (len(samples) - offset) // self.samples_per_bit
        if num_bits <= 0:
            return np.array([]), np.array([])

        # Pre-compute reference waveforms for one bit period
        t = np.arange(self.samples_per_bit, dtype=np.float64) / self.sample_rate
        mark_cos = np.cos(2 * np.pi * self.mark_freq * t)
        mark_sin = np.sin(2 * np.pi * self.mark_freq * t)
        space_cos = np.cos(2 * np.pi * self.space_freq * t)
        space_sin = np.sin(2 * np.pi * self.space_freq * t)

        mark_mags = np.empty(num_bits)
        space_mags = np.empty(num_bits)

        for i in range(num_bits):
            start = offset + i * self.samples_per_bit
            end = start + self.samples_per_bit
            bit_samples = samples[start:end].astype(np.float64)

            # Correlation with reference waveforms (matched filter)
            mc = np.dot(bit_samples, mark_cos)
            ms = np.dot(bit_samples, mark_sin)
            sc = np.dot(bit_samples, space_cos)
            ss = np.dot(bit_samples, space_sin)

            mark_mags[i] = mc * mc + ms * ms
            space_mags[i] = sc * sc + ss * ss

        return mark_mags, space_mags

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

        Uses sustained amplitude detection to avoid triggering on noise spikes.

        Args:
            samples: Audio samples
            threshold_ratio: Ratio of max amplitude to use as threshold

        Returns:
            Sample index where sustained signal starts
        """
        if len(samples) == 0:
            return 0

        # Find where amplitude exceeds threshold
        max_amp = np.max(np.abs(samples))
        if max_amp < 0.01:  # No signal
            return 0

        threshold = max_amp * threshold_ratio

        # Require sustained signal: use RMS over sliding windows
        window = self.samples_per_bit  # One bit period
        if len(samples) < window * 2:
            above = np.where(np.abs(samples) > threshold)[0]
            return max(0, above[0] - window) if len(above) > 0 else 0

        # Compute running RMS
        squared = samples ** 2
        cumsum = np.cumsum(squared)
        rms = np.sqrt((cumsum[window:] - cumsum[:-window]) / window)

        rms_threshold = threshold * 0.5  # RMS threshold is lower than peak
        above_rms = np.where(rms > rms_threshold)[0]

        if len(above_rms) == 0:
            # Fallback to single-sample threshold
            above = np.where(np.abs(samples) > threshold)[0]
            return max(0, above[0] - window) if len(above) > 0 else 0

        return max(0, above_rms[0])

    def _bits_from_envelopes(
        self, mark_env: np.ndarray, space_env: np.ndarray, offset: int
    ) -> list[int]:
        """Extract bit decisions from pre-computed envelopes at given offset."""
        bits = []
        num_bits = (len(mark_env) - offset) // self.samples_per_bit

        for i in range(num_bits):
            start = offset + i * self.samples_per_bit
            end = start + self.samples_per_bit

            mark_energy = np.mean(mark_env[start:end])
            space_energy = np.mean(space_env[start:end])

            bits.append(1 if mark_energy > space_energy else 0)

        return bits

    def _bits_from_envelopes_with_clock_recovery(
        self, mark_env: np.ndarray, space_env: np.ndarray, offset: int
    ) -> list[int]:
        """Extract bit decisions with zero-crossing clock recovery.

        Uses a floating-point position accumulator instead of fixed integer
        stride.  At each bit transition the actual zero crossing of
        (mark_env - space_env) is compared to the expected boundary, and a
        proportional correction shifts the next sampling window.  This
        compensates for sample-rate mismatch between speaker and mic clocks.
        """
        spb = self.samples_per_bit
        Kp = 0.3                        # proportional gain
        max_corr = 0.15 * spb           # max correction per transition

        diff = mark_env - space_env     # positive = mark, negative = space

        bits: list[int] = []
        pos = float(offset)             # floating-point accumulator
        prev_bit = None

        while pos + spb <= len(mark_env):
            # --- bit decision: centre 50 % of the window ---
            centre_start = int(pos + 0.25 * spb)
            centre_end   = int(pos + 0.75 * spb)
            centre_end   = min(centre_end, len(mark_env))
            if centre_start >= centre_end:
                break

            mark_energy  = np.mean(mark_env[centre_start:centre_end])
            space_energy = np.mean(space_env[centre_start:centre_end])
            bit = 1 if mark_energy > space_energy else 0
            bits.append(bit)

            # --- clock recovery at transitions ---
            if prev_bit is not None and bit != prev_bit:
                boundary = pos
                # search window: ±40 % of bit period around boundary
                win_lo = max(0, int(boundary - 0.4 * spb))
                win_hi = min(len(diff) - 1, int(boundary + 0.4 * spb))
                if win_hi - win_lo > 1:
                    seg = diff[win_lo:win_hi]
                    sign_changes = np.diff(np.sign(seg))
                    zc_indices = np.nonzero(sign_changes)[0]
                    if len(zc_indices) > 0:
                        zc_abs = zc_indices + win_lo
                        closest = zc_abs[np.argmin(np.abs(zc_abs - boundary))]
                        timing_error = closest - boundary
                        pos += np.clip(Kp * timing_error,
                                       -max_corr, max_corr)

            prev_bit = bit
            pos += spb

        return bits

    def _bits_from_goertzel(
        self, samples: np.ndarray, offset: int
    ) -> list[int]:
        """Extract bit decisions using matched filter (DFT correlation).

        Each bit period is evaluated independently - no filter memory
        from previous bits. Normalized to remove acoustic frequency
        response bias.
        """
        mark_mags, space_mags = self._dft_magnitudes(samples, offset)
        if len(mark_mags) == 0:
            return []

        # Normalize to remove frequency response bias
        mark_mean = np.mean(mark_mags) + 1e-10
        space_mean = np.mean(space_mags) + 1e-10
        mark_norm = mark_mags / mark_mean
        space_norm = space_mags / space_mean

        return [1 if m > s else 0 for m, s in zip(mark_norm, space_norm)]

    def _bits_to_bytes(self, bits: list[int]) -> bytes:
        """Convert bit list to bytes (LSB first)."""
        result = bytearray()
        for i in range(0, len(bits) - 7, 8):
            byte = 0
            for j in range(8):
                byte |= bits[i + j] << j
            result.append(byte)
        return bytes(result)

    def _score_alignment(self, data: bytes) -> int:
        """Score a demodulated byte sequence for preamble/sync quality."""
        if len(data) < 3:
            return -1

        # Count preamble bytes (0xAA) in first 20 bytes
        preamble_score = sum(1 for b in data[:20] if b == 0xAA)

        # Bonus for SYNC pattern (0x7E 0x7E) after preamble-like bytes
        sync_bonus = 0
        for i in range(4, min(24, len(data) - 1)):
            if data[i] == 0x7E and data[i + 1] == 0x7E:
                sync_bonus = 6
                break

        return preamble_score + sync_bonus

    def _demodulate_dft(self, samples: np.ndarray, offset: int) -> bytes:
        """Demodulate using per-bit DFT correlation with automatic threshold.

        Each bit is evaluated independently (no filter memory). The mark/space
        decision threshold is derived from the signal statistics, automatically
        adapting to any channel frequency response.
        """
        mark_mags, space_mags = self._dft_magnitudes(samples, offset)
        if len(mark_mags) == 0:
            return b''

        # Compute mark ratio for each bit: mark / (mark + space)
        total = mark_mags + space_mags
        mark_ratio = mark_mags / (total + 1e-10)

        # Find bits with actual signal (above noise floor)
        signal_threshold = np.max(total) * 0.05
        signal_mask = total > signal_threshold

        if np.sum(signal_mask) > 8:
            # Automatic threshold: find midpoint between mark and space clusters.
            # Sort ratios and use 25th/75th percentile to estimate cluster centers,
            # then threshold at the midpoint. Works regardless of mark/space balance.
            signal_ratios = mark_ratio[signal_mask]
            sorted_ratios = np.sort(signal_ratios)
            n = len(sorted_ratios)
            space_center = np.mean(sorted_ratios[:max(1, n // 4)])  # Bottom 25%
            mark_center = np.mean(sorted_ratios[-max(1, n // 4):])  # Top 25%
            threshold = (space_center + mark_center) / 2
        else:
            threshold = 0.5

        bits = [1 if r > threshold else 0 for r in mark_ratio]
        return self._bits_to_bytes(bits)

    def _demodulate_envelope(
        self, mark_env: np.ndarray, space_env: np.ndarray, offset: int
    ) -> bytes:
        """Demodulate using pre-computed RMS-normalized envelopes."""
        bits = self._bits_from_envelopes(mark_env, space_env, offset)
        return self._bits_to_bytes(bits)

    def _demodulate_envelope_recovered(
        self, mark_env: np.ndarray, space_env: np.ndarray, offset: int
    ) -> bytes:
        """Demodulate with clock-recovered bit extraction."""
        bits = self._bits_from_envelopes_with_clock_recovery(
            mark_env, space_env, offset
        )
        return self._bits_to_bytes(bits)

    def _demodulate_dft_recovered(
        self, samples: np.ndarray,
        mark_env: np.ndarray, space_env: np.ndarray,
        offset: int,
    ) -> bytes:
        """Demodulate with envelope clock recovery and DFT bit decisions.

        Uses envelope zero-crossings for timing recovery (handles clock
        drift), but makes per-bit mark/space decisions using stateless
        DFT correlation (immune to IIR filter ISI).  Mean-normalised
        magnitudes compensate for acoustic channel frequency response.
        """
        spb = self.samples_per_bit
        Kp = 0.3
        max_corr = 0.15 * spb
        diff = mark_env - space_env

        # --- Phase 1: clock recovery → bit positions ---
        positions: list[float] = []
        pos = float(offset)
        prev_bit = None

        while pos + spb <= len(mark_env):
            positions.append(pos)

            cs = int(pos + 0.25 * spb)
            ce = min(int(pos + 0.75 * spb), len(mark_env))
            if cs >= ce:
                break
            bit = (1 if np.mean(mark_env[cs:ce]) > np.mean(space_env[cs:ce])
                   else 0)

            if prev_bit is not None and bit != prev_bit:
                boundary = pos
                win_lo = max(0, int(boundary - 0.4 * spb))
                win_hi = min(len(diff) - 1, int(boundary + 0.4 * spb))
                if win_hi - win_lo > 1:
                    seg = diff[win_lo:win_hi]
                    sign_changes = np.diff(np.sign(seg))
                    zc_indices = np.nonzero(sign_changes)[0]
                    if len(zc_indices) > 0:
                        zc_abs = zc_indices + win_lo
                        closest = zc_abs[
                            np.argmin(np.abs(zc_abs - boundary))]
                        timing_error = closest - boundary
                        pos += np.clip(Kp * timing_error,
                                       -max_corr, max_corr)

            prev_bit = bit
            pos += spb

        if not positions:
            return b''

        # --- Phase 2: DFT correlation at recovered positions ---
        t = np.arange(spb, dtype=np.float64) / self.sample_rate
        mark_cos = np.cos(2 * np.pi * self.mark_freq * t)
        mark_sin = np.sin(2 * np.pi * self.mark_freq * t)
        space_cos = np.cos(2 * np.pi * self.space_freq * t)
        space_sin = np.sin(2 * np.pi * self.space_freq * t)

        mark_mags = []
        space_mags = []
        for p in positions:
            start = int(p)
            end = start + spb
            if end > len(samples):
                break
            seg = samples[start:end].astype(np.float64)
            mc = np.dot(seg, mark_cos)
            ms = np.dot(seg, mark_sin)
            sc = np.dot(seg, space_cos)
            ss = np.dot(seg, space_sin)
            mark_mags.append(mc * mc + ms * ms)
            space_mags.append(sc * sc + ss * ss)

        if not mark_mags:
            return b''

        # --- Phase 3: mean-normalised decisions ---
        mark_arr = np.array(mark_mags)
        space_arr = np.array(space_mags)
        mark_mean = np.mean(mark_arr) + 1e-10
        space_mean = np.mean(space_arr) + 1e-10

        bits = [1 if (mm / mark_mean) > (sm / space_mean) else 0
                for mm, sm in zip(mark_arr, space_arr)]
        return self._bits_to_bytes(bits)

    def _compute_normalized_envelopes(
        self, samples: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Compute mark/space envelopes with RMS normalization.

        RMS normalization compensates for acoustic channel frequency
        response (e.g. speakers that are louder at 2200 Hz than 1200 Hz).
        """
        mark_env = self._envelope_detect(samples, self.mark_filter)
        space_env = self._envelope_detect(samples, self.space_filter)

        # RMS normalize to equalize frequency response
        mark_rms = np.sqrt(np.mean(mark_env ** 2)) + 1e-10
        space_rms = np.sqrt(np.mean(space_env ** 2)) + 1e-10
        mark_env = mark_env / mark_rms
        space_env = space_env / space_rms

        return mark_env, space_env

    def demodulate(self, samples: np.ndarray, auto_sync: bool = True) -> bytes:
        """Demodulate audio samples into bytes.

        Uses bandpass envelope detection with RMS normalization to
        compensate for acoustic channel frequency response. Searches
        for optimal bit alignment using preamble pattern matching.

        Args:
            samples: Audio samples to demodulate
            auto_sync: If True, automatically find signal start and bit alignment

        Returns:
            Demodulated bytes
        """
        if len(samples) < self.samples_per_bit * 8:
            return b''

        # Compute RMS-normalized envelopes once for all offset searches
        mark_env, space_env = self._compute_normalized_envelopes(samples)

        if not auto_sync:
            return self._demodulate_envelope(mark_env, space_env, 0)

        # Find approximate signal start
        base_offset = self.find_signal_start(samples)

        # Coarse search: try offsets and score by preamble quality
        coarse_step = max(1, self.samples_per_bit // 4)
        search_start = max(0, base_offset)
        search_end = min(
            len(samples) - self.samples_per_bit * 8,
            base_offset + self.samples_per_bit * 8 * 16,  # 16 bytes forward
        )

        best_offset = search_start
        best_score = -1

        for offset in range(search_start, search_end, coarse_step):
            data = self._demodulate_envelope(mark_env, space_env, offset)
            score = self._score_alignment(data)
            if score > best_score:
                best_score = score
                best_offset = offset
                if score >= 18:
                    break

        # Fine search: refine around the best coarse offset
        if best_score > 0:
            fine_start = max(0, best_offset - coarse_step)
            fine_end = min(len(samples) - self.samples_per_bit * 8,
                          best_offset + coarse_step)
            fine_step = max(1, self.samples_per_bit // 16)

            for offset in range(fine_start, fine_end, fine_step):
                data = self._demodulate_envelope(mark_env, space_env, offset)
                score = self._score_alignment(data)
                if score > best_score:
                    best_score = score
                    best_offset = offset

        # Try both envelope (fast, handles most cases) and DFT (stateless,
        # no ISI) demodulation.  Return whichever scores better on preamble.
        env_result = self._demodulate_envelope_recovered(
            mark_env, space_env, best_offset)
        dft_result = self._demodulate_dft_recovered(
            samples, mark_env, space_env, best_offset)

        env_score = self._score_alignment(env_result)
        dft_score = self._score_alignment(dft_result)

        return dft_result if dft_score > env_score else env_result

    def _demodulate_raw(self, samples: np.ndarray) -> bytes:
        """Demodulate without sync detection."""
        if len(samples) < self.samples_per_bit * 8:
            return b''

        mark_env, space_env = self._compute_normalized_envelopes(samples)
        return self._demodulate_envelope(mark_env, space_env, 0)


def generate_test_tone(freq: float, duration: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Generate a test tone for debugging."""
    t = np.arange(int(duration * sample_rate)) / sample_rate
    return np.sin(2 * np.pi * freq * t).astype(np.float32)
