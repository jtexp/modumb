"""Transport Layer - Reliable delivery."""

__all__ = ["ReliableTransport", "SessionManager", "SessionState"]


def __getattr__(name):
    """Lazy imports (depend on datalink which depends on modem)."""
    if name == "ReliableTransport":
        from .reliable import ReliableTransport
        return ReliableTransport
    if name in ("SessionManager", "SessionState"):
        from .session import SessionManager, SessionState
        return SessionManager if name == "SessionManager" else SessionState
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
