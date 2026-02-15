"""Data Link Layer - Framing and CRC."""

from .frame import Frame, FrameType

__all__ = ["Frame", "FrameType", "Framer"]


def __getattr__(name):
    """Lazy import for Framer (requires modem which requires numpy)."""
    if name == "Framer":
        from .framer import Framer
        return Framer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
