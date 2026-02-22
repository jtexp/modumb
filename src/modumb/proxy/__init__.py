"""Proxy Layer - HTTP proxy over modem."""

__all__ = ["ProxyConfig", "LocalProxy", "RemoteRelay"]


def __getattr__(name):
    """Lazy imports (depend on http/transport which depend on modem)."""
    if name == "ProxyConfig":
        from .config import ProxyConfig
        return ProxyConfig
    if name == "LocalProxy":
        from .local_proxy import LocalProxy
        return LocalProxy
    if name == "RemoteRelay":
        from .remote_proxy import RemoteRelay
        return RemoteRelay
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
