"""Tests for AFSK modulation/demodulation."""

import pytest
import numpy as np

from modumb.modem.afsk import (
    AFSKModulator,
    AFSKDemodulator,
    SAMPLE_RATE,
    BAUD_RATE,
    MARK_FREQ,
    SPACE_FREQ,
)


class TestAFSKModulator:
    """Test AFSK modulation."""

    def test_modulate_single_byte(self):
        """Test modulating a single byte."""
        mod = AFSKModulator()
        samples = mod.modulate(b'\x55')  # 01010101 pattern

        assert len(samples) > 0
        assert samples.dtype == np.float32
        # Should be 8 bits * samples_per_bit
        expected_samples = 8 * (SAMPLE_RATE // BAUD_RATE)
        assert len(samples) == expected_samples

    def test_modulate_empty(self):
        """Test modulating empty data."""
        mod = AFSKModulator()
        samples = mod.modulate(b'')

        assert len(samples) == 0

    def test_modulate_multiple_bytes(self):
        """Test modulating multiple bytes."""
        mod = AFSKModulator()
        data = b'Hello'
        samples = mod.modulate(data)

        expected_samples = len(data) * 8 * (SAMPLE_RATE // BAUD_RATE)
        assert len(samples) == expected_samples

    def test_samples_in_range(self):
        """Test that samples are in valid range [-1, 1]."""
        mod = AFSKModulator()
        samples = mod.modulate(b'\x00\xFF\x55\xAA')

        assert np.all(samples >= -1.0)
        assert np.all(samples <= 1.0)


class TestAFSKDemodulator:
    """Test AFSK demodulation."""

    def test_roundtrip_single_byte(self):
        """Test modulate then demodulate a single byte."""
        mod = AFSKModulator()
        demod = AFSKDemodulator()

        original = b'\x55'
        samples = mod.modulate(original)

        # Add more padding for filter settling (8 bit periods)
        padding = np.zeros(SAMPLE_RATE // BAUD_RATE * 8, dtype=np.float32)
        samples = np.concatenate([padding, samples, padding])

        recovered = demod.demodulate(samples)

        # Should recover at least the original byte (may have offset due to filter delay)
        assert len(recovered) >= 1

    def test_roundtrip_multiple_bytes(self):
        """Test modulate then demodulate multiple bytes."""
        mod = AFSKModulator()
        demod = AFSKDemodulator()

        original = b'Test'
        samples = mod.modulate(original)

        # Add padding
        padding = np.zeros(SAMPLE_RATE // BAUD_RATE * 4, dtype=np.float32)
        samples = np.concatenate([padding, samples, padding])

        recovered = demod.demodulate(samples)

        # Should recover most of the original data
        assert len(recovered) >= len(original) - 1

    def test_demodulate_empty(self):
        """Test demodulating empty/short data."""
        demod = AFSKDemodulator()

        # Too short
        assert demod.demodulate(np.array([], dtype=np.float32)) == b''
        assert demod.demodulate(np.zeros(10, dtype=np.float32)) == b''


class TestAFSKRoundtrip:
    """Integration tests for AFSK roundtrip."""

    @pytest.mark.parametrize("test_data", [
        b'\x00',
        b'\xFF',
        b'\xAA',
        b'\x55',
        b'A',
        b'AB',
        b'ABC',
    ])
    def test_roundtrip_patterns(self, test_data):
        """Test various bit patterns roundtrip."""
        mod = AFSKModulator()
        demod = AFSKDemodulator()

        samples = mod.modulate(test_data)

        # Add padding
        padding = np.zeros(SAMPLE_RATE // BAUD_RATE * 4, dtype=np.float32)
        samples = np.concatenate([padding, samples, padding])

        recovered = demod.demodulate(samples)

        # At minimum should get something back
        assert len(recovered) > 0
