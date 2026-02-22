"""Audio profiles for different connection modes.

Presets for acoustic (over-the-air), cable (3.5mm audio cable),
and loopback (in-memory testing) modes.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AudioProfile:
    """Audio transmission parameters for a connection mode."""
    name: str
    tx_volume: float
    echo_guard_time: float      # Seconds to suppress echo after TX
    lead_silence: float          # Seconds of silence before TX
    trail_silence: float         # Seconds of silence after TX
    hdmi_wake_enabled: bool      # Intel HDMI sleep bug workaround

    def __str__(self) -> str:
        return self.name


# Over-the-air acoustic transmission (speaker → microphone)
ACOUSTIC = AudioProfile(
    name="acoustic",
    tx_volume=0.08,
    echo_guard_time=0.08,
    lead_silence=0.3,
    trail_silence=0.2,
    hdmi_wake_enabled=True,
)

# Direct audio cable (3.5mm line-out → line-in)
CABLE = AudioProfile(
    name="cable",
    tx_volume=0.5,
    echo_guard_time=0.0,
    lead_silence=0.1,
    trail_silence=0.1,
    hdmi_wake_enabled=False,
)

# In-memory loopback for testing
LOOPBACK = AudioProfile(
    name="loopback",
    tx_volume=1.0,
    echo_guard_time=0.0,
    lead_silence=0.0,
    trail_silence=0.0,
    hdmi_wake_enabled=False,
)

PROFILES = {
    "acoustic": ACOUSTIC,
    "cable": CABLE,
    "loopback": LOOPBACK,
}


def get_profile(name: str = None) -> AudioProfile:
    """Get an audio profile by name.

    Args:
        name: Profile name, or None to use MODEM_MODE env var (default: acoustic)

    Returns:
        AudioProfile instance
    """
    if name is None:
        name = os.environ.get("MODEM_MODE", "acoustic")
    name = name.lower()
    if name not in PROFILES:
        raise ValueError(f"Unknown profile: {name!r} (available: {', '.join(PROFILES)})")
    return PROFILES[name]
