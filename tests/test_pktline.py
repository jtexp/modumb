"""Tests for Git pkt-line format."""

import pytest

from modumb.http.pktline import PktLine, FLUSH_PKT


class TestPktLine:
    """Test pkt-line encoding/decoding."""

    def test_encode_line(self):
        """Test encoding a single line."""
        data = b'hello\n'
        encoded = PktLine.encode_line(data)

        # Length is 4 (prefix) + 6 (data) = 10 = 0x000a
        assert encoded == b'000ahello\n'

    def test_encode_short_line(self):
        """Test encoding a short line."""
        data = b'x'
        encoded = PktLine.encode_line(data)

        # Length is 4 + 1 = 5 = 0x0005
        assert encoded == b'0005x'

    def test_encode_flush(self):
        """Test flush packet."""
        assert PktLine.encode_flush() == b'0000'

    def test_encode_delim(self):
        """Test delimiter packet."""
        assert PktLine.encode_delim() == b'0001'

    def test_encode_lines_with_flush(self):
        """Test encoding multiple lines with flush."""
        lines = [b'line1\n', b'line2\n']
        encoded = PktLine.encode_lines(lines, flush=True)

        assert encoded == b'000aline1\n' b'000aline2\n' b'0000'

    def test_encode_lines_without_flush(self):
        """Test encoding multiple lines without flush."""
        lines = [b'a', b'b']
        encoded = PktLine.encode_lines(lines, flush=False)

        assert encoded == b'0005a' b'0005b'

    def test_decode_line(self):
        """Test decoding a single line."""
        data = b'000ahello\n'
        line, remaining = PktLine.decode_line(data)

        assert line == b'hello\n'
        assert remaining == b''

    def test_decode_line_with_remaining(self):
        """Test decoding with remaining data."""
        data = b'0005x' b'0005y'
        line, remaining = PktLine.decode_line(data)

        assert line == b'x'
        assert remaining == b'0005y'

    def test_decode_flush(self):
        """Test decoding flush packet."""
        data = b'0000more'
        line, remaining = PktLine.decode_line(data)

        assert line is None
        assert remaining == b'more'

    def test_decode_lines_generator(self):
        """Test decoding multiple lines."""
        data = b'0005a' b'0005b' b'0000' b'0005c'
        lines = list(PktLine.decode_lines(data))

        assert lines == [b'a', b'b', None, b'c']

    def test_decode_all(self):
        """Test decode_all skips special packets."""
        data = b'0005a' b'0000' b'0005b'
        lines = PktLine.decode_all(data)

        assert lines == [b'a', b'b']

    def test_decode_incomplete_header(self):
        """Test decoding with incomplete header."""
        with pytest.raises(ValueError):
            PktLine.decode_line(b'00')

    def test_decode_incomplete_body(self):
        """Test decoding with incomplete body."""
        with pytest.raises(ValueError):
            PktLine.decode_line(b'0010short')

    def test_decode_invalid_length(self):
        """Test decoding with invalid hex length."""
        with pytest.raises(ValueError):
            PktLine.decode_line(b'ZZZZ')

    def test_encode_too_long(self):
        """Test encoding data that's too long."""
        # 65535 - 4 = 65531 max payload
        with pytest.raises(ValueError):
            PktLine.encode_line(b'x' * 65532)

    def test_parse_capability_advertisement(self):
        """Test parsing capability advertisement."""
        # Simulated git-upload-pack output
        # Build with correct lengths
        line1 = b'abcd1234 refs/heads/main\x00multi_ack thin-pack side-band-64k ofs-delta\n'
        line2 = b'ef567890 refs/heads/feature\n'
        service_line = b'# service=git-upload-pack\n'

        data = (
            PktLine.encode_line(service_line) +
            PktLine.encode_flush() +
            PktLine.encode_line(line1) +
            PktLine.encode_line(line2) +
            PktLine.encode_flush()
        )

        result = PktLine.parse_capability_advertisement(data)

        assert 'refs/heads/main' in result['refs']
        assert result['refs']['refs/heads/main'] == 'abcd1234'
        assert 'refs/heads/feature' in result['refs']
        assert 'multi_ack' in result['capabilities']
        assert 'thin-pack' in result['capabilities']

    def test_build_want_request(self):
        """Test building want request."""
        refs = ['abcd1234', 'ef567890']
        caps = ['multi_ack', 'thin-pack']

        request = PktLine.build_want_request(refs, caps)

        # Should contain want lines
        assert b'want abcd1234' in request
        assert b'want ef567890' in request
        # First line should have capabilities
        assert b'multi_ack' in request
        # Should end with done and flush
        assert b'done' in request
        assert b'0000' in request
