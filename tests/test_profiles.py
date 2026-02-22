"""Tests for audio profiles."""

import os
import pytest

from modumb.modem.profiles import (
    AudioProfile, ACOUSTIC, CABLE, LOOPBACK, PROFILES, get_profile,
)


class TestAudioProfile:
    """Test AudioProfile dataclass and presets."""

    def test_acoustic_defaults(self):
        assert ACOUSTIC.name == "acoustic"
        assert ACOUSTIC.tx_volume == 0.08
        assert ACOUSTIC.echo_guard_time == 0.08
        assert ACOUSTIC.lead_silence == 0.3
        assert ACOUSTIC.trail_silence == 0.2
        assert ACOUSTIC.hdmi_wake_enabled is True

    def test_cable_defaults(self):
        assert CABLE.name == "cable"
        assert CABLE.tx_volume == 0.5
        assert CABLE.echo_guard_time == 0.0
        assert CABLE.lead_silence == 0.1
        assert CABLE.trail_silence == 0.1
        assert CABLE.hdmi_wake_enabled is False

    def test_loopback_defaults(self):
        assert LOOPBACK.name == "loopback"
        assert LOOPBACK.tx_volume == 1.0
        assert LOOPBACK.echo_guard_time == 0.0
        assert LOOPBACK.lead_silence == 0.0
        assert LOOPBACK.trail_silence == 0.0
        assert LOOPBACK.hdmi_wake_enabled is False

    def test_profiles_dict(self):
        assert set(PROFILES.keys()) == {"acoustic", "cable", "loopback"}

    def test_frozen(self):
        """Profiles are immutable."""
        with pytest.raises(AttributeError):
            ACOUSTIC.tx_volume = 0.5  # type: ignore[misc]

    def test_str(self):
        assert str(ACOUSTIC) == "acoustic"
        assert str(CABLE) == "cable"


class TestGetProfile:
    """Test get_profile() lookup."""

    def test_by_name(self):
        assert get_profile("acoustic") is ACOUSTIC
        assert get_profile("cable") is CABLE
        assert get_profile("loopback") is LOOPBACK

    def test_case_insensitive(self):
        assert get_profile("CABLE") is CABLE
        assert get_profile("Loopback") is LOOPBACK

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown profile"):
            get_profile("satellite")

    def test_env_var(self, monkeypatch):
        monkeypatch.setenv("MODEM_MODE", "cable")
        assert get_profile() is CABLE

    def test_env_default_acoustic(self, monkeypatch):
        monkeypatch.delenv("MODEM_MODE", raising=False)
        assert get_profile() is ACOUSTIC
