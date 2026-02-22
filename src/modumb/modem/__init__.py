"""Physical Layer - AFSK modem implementation."""

from .profiles import AudioProfile, ACOUSTIC, CABLE, LOOPBACK, get_profile

__all__ = [
    "AFSKModulator", "AFSKDemodulator", "AudioInterface", "Modem",
    "AudioProfile", "ACOUSTIC", "CABLE", "LOOPBACK", "get_profile",
]


def __getattr__(name):
    """Lazy imports (all require numpy/scipy)."""
    if name in ("AFSKModulator", "AFSKDemodulator"):
        from .afsk import AFSKModulator, AFSKDemodulator
        return AFSKModulator if name == "AFSKModulator" else AFSKDemodulator
    if name == "AudioInterface":
        from .audio_io import AudioInterface
        return AudioInterface
    if name == "Modem":
        from .modem import Modem
        return Modem
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
