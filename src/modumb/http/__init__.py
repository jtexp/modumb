"""HTTP Layer - HTTP client/server over modem."""

from .pktline import PktLine

__all__ = ["PktLine", "HttpClient", "HttpServer"]


def __getattr__(name):
    """Lazy imports (depend on transport which depends on modem)."""
    if name == "HttpClient":
        from .client import HttpClient
        return HttpClient
    if name == "HttpServer":
        from .server import HttpServer
        return HttpServer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
