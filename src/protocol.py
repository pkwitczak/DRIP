"""
WebSocket framing for DRIP.

Used by client.py after the HTTP upgrade handshake.

Features:
- Text and binary messages.
- Fragmented incoming messages.
- PING/PONG handling.
- Bounded incoming frame and message sizes.
- Partial socket reads and writes.
- Client-side masking.

This module does not implement the HTTP upgrade handshake or TLS.
Use blocking sockets. A socket timeout or I/O error closes the connection
because the stream may already contain a partially consumed frame.
"""

import os
import struct
from collections import namedtuple


OP_CONT = 0x0
OP_TEXT = 0x1
OP_BYTES = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

CLOSE_OK = 1000
CLOSE_GOING_AWAY = 1001
CLOSE_PROTOCOL_ERROR = 1002
CLOSE_DATA_NOT_SUPPORTED = 1003
CLOSE_BAD_DATA = 1007
CLOSE_POLICY_VIOLATION = 1008
CLOSE_TOO_BIG = 1009
CLOSE_MISSING_EXTN = 1010
CLOSE_BAD_CONDITION = 1011

DEFAULT_MAX_SIZE = 16 * 1024

URI = namedtuple("URI", ("protocol", "hostname", "port", "path"))

_DATA_OPCODES = (OP_CONT, OP_TEXT, OP_BYTES)
_CONTROL_OPCODES = (OP_CLOSE, OP_PING, OP_PONG)
_VALID_OPCODES = _DATA_OPCODES + _CONTROL_OPCODES


class NoDataException(Exception):
    """Retained for compatibility with earlier application code."""


class ConnectionClosed(Exception):
    """The connection is closed or could not complete an I/O operation."""


class ProtocolError(ConnectionClosed):
    """The peer sent an invalid or unsupported WebSocket frame."""


class MessageTooBig(ConnectionClosed):
    """An incoming frame or assembled message exceeds the configured limit."""


def urlparse(uri):
    """
    Parse a ws:// or wss:// URL.

    Supports DNS names, IPv4 addresses, and bracketed IPv6 addresses.
    User information and URL fragments are not supported.
    """
    if not isinstance(uri, str):
        raise TypeError("WebSocket URL must be a string")

    if any(ord(char) <= 32 or ord(char) == 127 for char in uri):
        raise ValueError("Whitespace and control characters are not allowed")

    if "#" in uri:
        raise ValueError("URL fragments are not supported")

    if "://" not in uri:
        raise ValueError("WebSocket URL requires ws:// or wss://")

    scheme, rest = uri.split("://", 1)
    scheme = scheme.lower()

    if scheme not in ("ws", "wss"):
        raise ValueError("Unsupported WebSocket URL scheme")

    end = len(rest)
    for separator in ("/", "?"):
        position = rest.find(separator)
        if position >= 0:
            end = min(end, position)

    authority = rest[:end]
    path = rest[end:] or "/"

    if path.startswith("?"):
        path = "/" + path

    if not authority or "@" in authority:
        raise ValueError("Invalid WebSocket URL authority")

    port = 443 if scheme == "wss" else 80

    if authority.startswith("["):
        closing = authority.find("]")

        if closing < 0:
            raise ValueError("Invalid IPv6 address")

        host = authority[1:closing]
        suffix = authority[closing + 1:]

        if suffix:
            if not suffix.startswith(":") or not suffix[1:].isdigit():
                raise ValueError("Invalid WebSocket port")
            port = int(suffix[1:])
    else:
        if authority.count(":") > 1:
            raise ValueError("IPv6 addresses must be enclosed in brackets")

        if ":" in authority:
            host, port_text = authority.rsplit(":", 1)

            if not port_text.isdigit():
                raise ValueError("Invalid WebSocket port")

            port = int(port_text)
        else:
            host = authority

    if not host:
        raise ValueError("WebSocket hostname is missing")

    if not 1 <= port <= 65535:
        raise ValueError("WebSocket port is out of range")

    return URI(scheme, host, port, path)


def _valid_close_code(code):
    return (
        code in (
            1000, 1001, 1002, 1003,
            1007, 1008, 1009, 1010,
            1011, 1012, 1013, 1014,
        )
        or 3000 <= code <= 4999
    )


class Websocket:
    is_client = False

    def __init__(self, sock, max_size=DEFAULT_MAX_SIZE):
        if not isinstance(max_size, int) or max_size <= 0:
            raise ValueError("max_size must be a positive integer")

        self.sock = sock
        self.open = True
        self.max_size = max_size
        self._close_sent = False

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def settimeout(self, timeout):
        self._ensure_open()
        self.sock.settimeout(timeout)

    def _ensure_open(self):
        if not self.open:
            raise ConnectionClosed("WebSocket is closed")

    def _read_exact(self, size):
        if size == 0:
            return b""

        result = bytearray()

        while len(result) < size:
            chunk = self.sock.read(size - len(result))

            if chunk is None:
                raise OSError("Socket read returned no data; use blocking mode")

            if chunk == b"":
                raise ConnectionClosed("Peer closed the TCP connection")

            result.extend(chunk)

        return bytes(result)

    def _write_all(self, data):
        view = memoryview(data)
        offset = 0

        while offset < len(view):
            written = self.sock.write(view[offset:])

            if written is None or written <= 0:
                raise OSError("Socket write did not make progress")

            offset += written

    def _fail(self, code, message, error_class=ProtocolError):
        try:
            self.close(code=code)
        finally:
            raise error_class(message)

    def read_frame(self, max_size=None):
        self._ensure_open()

        limit = self.max_size

        if max_size is not None:
            if not isinstance(max_size, int) or max_size < 0:
                raise ValueError("max_size must be a non-negative integer")
            limit = min(limit, max_size)

        try:
            byte1, byte2 = self._read_exact(2)

            fin = bool(byte1 & 0x80)
            opcode = byte1 & 0x0F
            masked = bool(byte2 & 0x80)
            length_marker = byte2 & 0x7F

            if byte1 & 0x70:
                self._fail(
                    CLOSE_PROTOCOL_ERROR,
                    "WebSocket extensions are not supported",
                )

            if opcode not in _VALID_OPCODES:
                self._fail(CLOSE_PROTOCOL_ERROR, "Invalid frame opcode")

            if masked != (not self.is_client):
                self._fail(CLOSE_PROTOCOL_ERROR, "Invalid frame masking")

            is_control = opcode in _CONTROL_OPCODES

            if is_control and (not fin or length_marker > 125):
                self._fail(
                    CLOSE_PROTOCOL_ERROR,
                    "Invalid control frame",
                )

            length = length_marker

            if length_marker == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]

                if length < 126:
                    self._fail(
                        CLOSE_PROTOCOL_ERROR,
                        "Non-minimal frame length encoding",
                    )

            elif length_marker == 127:
                encoded = self._read_exact(8)

                if encoded[0] & 0x80:
                    self._fail(
                        CLOSE_PROTOCOL_ERROR,
                        "Invalid 64-bit frame length",
                    )

                length = struct.unpack("!Q", encoded)[0]

                if length < 65536:
                    self._fail(
                        CLOSE_PROTOCOL_ERROR,
                        "Non-minimal frame length encoding",
                    )

            # Control frames have their own protocol limit and must remain
            # readable even when the remaining message budget is zero.
            if not is_control and length > limit:
                self._fail(
                    CLOSE_TOO_BIG,
                    "Incoming frame exceeds size limit",
                    MessageTooBig,
                )

            mask = self._read_exact(4) if masked else None
            data = self._read_exact(length)

            if masked:
                data = bytes(
                    value ^ mask[index % 4]
                    for index, value in enumerate(data)
                )

            return fin, opcode, data

        except ConnectionClosed:
            self._close()
            raise
        except (OSError, MemoryError) as exc:
            self._close()
            raise ConnectionClosed("Frame read failed: " + str(exc))

    def write_frame(self, opcode, data=b""):
        self._ensure_open()

        if opcode not in _VALID_OPCODES:
            raise ValueError("Invalid frame opcode")

        if self._close_sent:
            raise ConnectionClosed("A close frame has already been sent")

        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("Frame data must be bytes-like")

        length = len(data)

        if opcode in _CONTROL_OPCODES and length > 125:
            raise ValueError("Control frame payload exceeds 125 bytes")

        if length >= (1 << 63):
            raise ValueError("Frame payload is too large")

        byte1 = 0x80 | opcode
        mask_flag = 0x80 if self.is_client else 0

        if length < 126:
            header = struct.pack("!BB", byte1, mask_flag | length)
        elif length < 65536:
            header = struct.pack("!BBH", byte1, mask_flag | 126, length)
        else:
            header = struct.pack("!BBQ", byte1, mask_flag | 127, length)

        # Prepare masking before writing any part of the frame.
        mask = None
        if self.is_client:
            mask = os.urandom(4)
            data = bytes(
                value ^ mask[index % 4]
                for index, value in enumerate(data)
            )

        try:
            self._write_all(header)

            if mask is not None:
                self._write_all(mask)

            self._write_all(data)

            if opcode == OP_CLOSE:
                self._close_sent = True

        except (OSError, MemoryError) as exc:
            self._close()
            raise ConnectionClosed("Frame write failed: " + str(exc))

    def _validate_close_payload(self, data):
        if len(data) == 1:
            self._fail(
                CLOSE_PROTOCOL_ERROR,
                "Close payload must be empty or at least two bytes",
            )

        if not data:
            return

        code = struct.unpack("!H", data[:2])[0]

        if not _valid_close_code(code):
            self._fail(CLOSE_PROTOCOL_ERROR, "Invalid close status code")

        try:
            data[2:].decode("utf-8")
        except UnicodeError:
            self._fail(CLOSE_BAD_DATA, "Invalid UTF-8 close reason")

    def _decode_message(self, opcode, data):
        if opcode == OP_BYTES:
            return data

        try:
            return data.decode("utf-8")
        except UnicodeError:
            self._fail(CLOSE_BAD_DATA, "Invalid UTF-8 text message")

    def recv(self):
        """
        Receive one complete message.

        Returns:
            str: text message
            bytes: binary message
            None: peer completed a close request

        Raises ConnectionClosed on unexpected EOF or socket failure.
        Empty messages remain valid messages, not disconnect indicators.
        """
        self._ensure_open()

        message_opcode = None
        fragments = None

        while self.open:
            remaining = (
                self.max_size
                if fragments is None
                else self.max_size - len(fragments)
            )

            fin, opcode, data = self.read_frame(max_size=remaining)

            if opcode == OP_PING:
                self.write_frame(OP_PONG, data)
                continue

            if opcode == OP_PONG:
                continue

            if opcode == OP_CLOSE:
                self._validate_close_payload(data)

                try:
                    if not self._close_sent:
                        self.write_frame(OP_CLOSE, data)
                finally:
                    self._close()

                return None

            if opcode == OP_CONT:
                if message_opcode is None:
                    self._fail(
                        CLOSE_PROTOCOL_ERROR,
                        "Unexpected continuation frame",
                    )

                try:
                    fragments.extend(data)
                except MemoryError:
                    self._fail(
                        CLOSE_TOO_BIG,
                        "Not enough memory for fragmented message",
                        MessageTooBig,
                    )

                if fin:
                    return self._decode_message(
                        message_opcode,
                        fragments,
                    )

                continue

            if message_opcode is not None:
                self._fail(
                    CLOSE_PROTOCOL_ERROR,
                    "New data frame before fragmented message completed",
                )

            if fin:
                return self._decode_message(opcode, data)

            message_opcode = opcode

            try:
                fragments = bytearray(data)
            except MemoryError:
                self._fail(
                    CLOSE_TOO_BIG,
                    "Not enough memory for fragmented message",
                    MessageTooBig,
                )

        raise ConnectionClosed("WebSocket is closed")

    def send(self, buf):
        self._ensure_open()

        if isinstance(buf, str):
            opcode = OP_TEXT
            data = buf.encode("utf-8")
        elif isinstance(buf, (bytes, bytearray, memoryview)):
            opcode = OP_BYTES
            data = buf
        else:
            raise TypeError("Message must be str or bytes-like")

        if len(data) > self.max_size:
            raise ValueError("Outgoing message exceeds size limit")

        self.write_frame(opcode, data)

    def ping(self, data=b""):
        self.write_frame(OP_PING, data)

    def close(self, code=CLOSE_OK, reason=""):
        """
        Send CLOSE and release the socket.

        This is immediate cleanup, not a wait for the peer's CLOSE reply.
        """
        if not self.open:
            return

        if not _valid_close_code(code):
            raise ValueError("Invalid close status code")

        if not isinstance(reason, str):
            raise TypeError("Close reason must be a string")

        reason_bytes = reason.encode("utf-8")

        if len(reason_bytes) > 123:
            raise ValueError("Close reason exceeds 123 UTF-8 bytes")

        try:
            if not self._close_sent:
                payload = struct.pack("!H", code) + reason_bytes
                self.write_frame(OP_CLOSE, payload)
        except (ConnectionClosed, OSError, MemoryError):
            pass
        finally:
            self._close()

    def _close(self):
        if not self.open:
            return

        self.open = False

        try:
            self.sock.close()
        except OSError:
            pass