"""Git Integration - Remote helper and smart HTTP."""

__all__ = ["GitSmartHttpClient", "GitSmartHttpServer", "GitRemoteHelper"]


def __getattr__(name):
    """Lazy imports (depend on http/transport which depend on modem)."""
    if name in ("GitSmartHttpClient", "GitSmartHttpServer"):
        from .smart_http import GitSmartHttpClient, GitSmartHttpServer
        return GitSmartHttpClient if name == "GitSmartHttpClient" else GitSmartHttpServer
    if name == "GitRemoteHelper":
        from .remote_helper import GitRemoteHelper
        return GitRemoteHelper
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
