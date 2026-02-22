"""Proxy configuration."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProxyConfig:
    """Configuration for the HTTP proxy system."""

    # Network
    listen_host: str = "127.0.0.1"
    listen_port: int = 8080

    # Modem
    mode: str = "acoustic"          # acoustic, cable, loopback
    baud_rate: int = 300            # Modem baud rate (300, 1200)
    duplex: str = "half"            # half or full (full requires cable/loopback)
    input_device: Optional[int] = None
    output_device: Optional[int] = None
    audible: bool = False

    # Relay limits
    max_response_size: int = 1_048_576  # 1 MB
    request_timeout: float = 60.0       # Seconds to wait for upstream response
    allowed_hosts: Optional[list[str]] = None  # None = allow all
