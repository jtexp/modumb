"""Tests for proxy module."""

import struct
import pytest
from unittest.mock import patch, MagicMock
import urllib.error

from modumb.proxy.config import ProxyConfig
from modumb.proxy.remote_proxy import create_relay_handler, create_connect_handler, HOP_BY_HOP
from modumb.http.server import HttpServerRequest, HttpServerResponse


class TestProxyConfig:
    """Test ProxyConfig defaults."""

    def test_defaults(self):
        config = ProxyConfig()
        assert config.listen_host == "127.0.0.1"
        assert config.listen_port == 8080
        assert config.mode == "acoustic"
        assert config.max_response_size == 1_048_576
        assert config.allowed_hosts is None

    def test_custom(self):
        config = ProxyConfig(listen_port=9090, mode="cable", allowed_hosts=["example.com"])
        assert config.listen_port == 9090
        assert config.mode == "cable"
        assert config.allowed_hosts == ["example.com"]


class TestRelayHandler:
    """Test the remote relay request handler."""

    def _make_request(self, method="GET", path="http://example.com/test",
                      headers=None, body=b""):
        if headers is None:
            headers = {"host": "example.com"}
        return HttpServerRequest(
            method=method,
            path=path,
            headers=headers,
            body=body,
        )

    def test_connect_returns_200(self):
        config = ProxyConfig()
        handler = create_relay_handler(config)
        req = self._make_request(method="CONNECT", path="example.com:443")
        resp = handler(req)
        assert resp.status_code == 200
        assert resp.body == b""

    def test_connect_checks_allowed_hosts(self):
        config = ProxyConfig(allowed_hosts=["allowed.com"])
        handler = create_relay_handler(config)
        req = self._make_request(method="CONNECT", path="blocked.com:443")
        resp = handler(req)
        assert resp.status_code == 403

    def test_connect_allows_permitted_host(self):
        config = ProxyConfig(allowed_hosts=["example.com"])
        handler = create_relay_handler(config)
        req = self._make_request(method="CONNECT", path="example.com:443")
        resp = handler(req)
        assert resp.status_code == 200

    def test_allowed_hosts_blocks(self):
        config = ProxyConfig(allowed_hosts=["allowed.com"])
        handler = create_relay_handler(config)
        req = self._make_request(path="http://blocked.com/foo")
        resp = handler(req)
        assert resp.status_code == 403

    def test_allowed_hosts_permits(self):
        config = ProxyConfig(allowed_hosts=["example.com"])
        handler = create_relay_handler(config)
        req = self._make_request(path="http://example.com/foo")

        # Mock urlopen so we don't make real requests
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"OK"
        mock_resp.status = 200
        mock_resp.reason = "OK"
        mock_resp.getheaders.return_value = [("Content-Type", "text/plain")]
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("modumb.proxy.remote_proxy.urllib.request.urlopen", return_value=mock_resp):
            resp = handler(req)
        assert resp.status_code == 200
        assert resp.body == b"OK"

    def test_relative_path_uses_host_header(self):
        config = ProxyConfig()
        handler = create_relay_handler(config)
        req = self._make_request(path="/page", headers={"host": "example.com"})

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"page content"
        mock_resp.status = 200
        mock_resp.reason = "OK"
        mock_resp.getheaders.return_value = []
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("modumb.proxy.remote_proxy.urllib.request.urlopen", return_value=mock_resp) as mock_open:
            resp = handler(req)
        # Should have constructed http://example.com/page
        called_req = mock_open.call_args[0][0]
        assert called_req.full_url == "http://example.com/page"

    def test_missing_host_returns_400(self):
        config = ProxyConfig()
        handler = create_relay_handler(config)
        req = self._make_request(path="/no-host", headers={})
        resp = handler(req)
        assert resp.status_code == 400

    def test_upstream_http_error(self):
        config = ProxyConfig()
        handler = create_relay_handler(config)
        req = self._make_request()

        error = urllib.error.HTTPError(
            "http://example.com/test", 404, "Not Found", {"Content-Type": "text/plain"}, None
        )
        error.read = MagicMock(return_value=b"not found")

        with patch("modumb.proxy.remote_proxy.urllib.request.urlopen", side_effect=error):
            resp = handler(req)
        assert resp.status_code == 404

    def test_upstream_url_error(self):
        config = ProxyConfig()
        handler = create_relay_handler(config)
        req = self._make_request()

        error = urllib.error.URLError("DNS failure")

        with patch("modumb.proxy.remote_proxy.urllib.request.urlopen", side_effect=error):
            resp = handler(req)
        assert resp.status_code == 502
        assert b"DNS failure" in resp.body

    def test_hop_by_hop_stripped(self):
        config = ProxyConfig()
        handler = create_relay_handler(config)
        req = self._make_request(
            headers={
                "host": "example.com",
                "connection": "keep-alive",
                "transfer-encoding": "chunked",
                "accept": "text/html",
            }
        )

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"data"
        mock_resp.status = 200
        mock_resp.reason = "OK"
        mock_resp.getheaders.return_value = [
            ("Content-Type", "text/html"),
            ("Transfer-Encoding", "chunked"),
        ]
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("modumb.proxy.remote_proxy.urllib.request.urlopen", return_value=mock_resp) as mock_open:
            resp = handler(req)

        # Verify hop-by-hop headers not sent upstream
        called_req = mock_open.call_args[0][0]
        for h in HOP_BY_HOP:
            assert h not in [k.lower() for k in called_req.headers]

        # Verify hop-by-hop headers stripped from response
        for h in resp.headers:
            assert h.lower() not in HOP_BY_HOP


class TestConnectHandler:
    """Test create_connect_handler."""

    def test_opens_tcp_to_correct_host_port(self):
        config = ProxyConfig()
        handler = create_connect_handler(config)
        session = MagicMock()
        # Close signal immediately so handler exits after connecting
        close_chunk = struct.pack('<I', 0)
        session.receive = MagicMock(return_value=close_chunk)

        with patch("modumb.proxy.remote_proxy.socket.create_connection") as mock_conn:
            mock_sock = MagicMock()
            mock_conn.return_value = mock_sock
            handler(session, "example.com:8443")
            mock_conn.assert_called_once_with(("example.com", 8443), timeout=10)
            mock_sock.close.assert_called_once()

    def test_defaults_to_port_443(self):
        config = ProxyConfig()
        handler = create_connect_handler(config)
        session = MagicMock()
        close_chunk = struct.pack('<I', 0)
        session.receive = MagicMock(return_value=close_chunk)

        with patch("modumb.proxy.remote_proxy.socket.create_connection") as mock_conn:
            mock_sock = MagicMock()
            mock_conn.return_value = mock_sock
            handler(session, "example.com")
            mock_conn.assert_called_once_with(("example.com", 443), timeout=10)

    def test_sends_close_on_upstream_connect_failure(self):
        config = ProxyConfig()
        handler = create_connect_handler(config)
        session = MagicMock()
        session.send = MagicMock(return_value=True)

        with patch("modumb.proxy.remote_proxy.socket.create_connection",
                    side_effect=ConnectionRefusedError("refused")):
            handler(session, "example.com:443")
        # Should have sent a close signal (4 zero bytes)
        session.send.assert_called_once()
        sent = session.send.call_args[0][0]
        assert sent == struct.pack('<I', 0)


class TestHopByHop:
    """Test hop-by-hop header set."""

    def test_contains_standard_headers(self):
        assert "connection" in HOP_BY_HOP
        assert "transfer-encoding" in HOP_BY_HOP
        assert "keep-alive" in HOP_BY_HOP

    def test_does_not_contain_end_to_end(self):
        assert "content-type" not in HOP_BY_HOP
        assert "content-length" not in HOP_BY_HOP
