# MIT License
#
# Copyright (c) 2026 hrksgt
# Copyright (c) 2026 TWO PLUS THREE LIMITED
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""CyberPi wire protocol, ported from weare2plus3/cyberpi-uploader."""

import struct
from collections.abc import Callable, Iterator
from dataclasses import dataclass

F3 = 0xF3
F4 = 0xF4
CHUNK_SIZE = 80
STAGING_PATH = "/flash/_xx_main.py"


def frame(body: bytes) -> bytes:
    """Encode a body with the reference header and body checksums.

    Args:
        body: Payload to enclose in a wire frame.

    Returns:
        The complete wire frame.

    Raises:
        ValueError: If the body exceeds the 16-bit length field.
    """
    if len(body) > 0xFFFF:
        raise ValueError("Frame body too large")
    low = len(body) & 0xFF
    high = len(body) >> 8
    return (
        bytes((F3, (F3 + low + high) & 0xFF, low, high))
        + body
        + bytes((sum(body) & 0xFF, F4))
    )


def exec_frame(code: str) -> bytes:
    """Encode a short Python snippet for execution on the board.

    Args:
        code: Python source to execute.

    Returns:
        A TYPE_SCRIPT wire frame.
    """
    source = code.encode("utf-8")
    return frame(b"\x28\x04\x00\x00" + struct.pack("<H", len(source)) + source)


PING = frame(b"\x0d\x00\x00")
EXEC_IMPORT_CONFIG = exec_frame("try:\n    import config\nexcept:\n    pass")
EXEC_REPL_OFF = exec_frame(
    'try:\n    config.write_config("repl_enable", False)\nexcept:\n    pass'
)


def file_checksum(data: bytes) -> bytes:
    """Compute the XOR of zero-padded little-endian 32-bit words.

    Args:
        data: File bytes, including wire padding.

    Returns:
        The four-byte little-endian checksum.
    """
    checksum = 0
    for offset in range(0, len(data), 4):
        checksum ^= int.from_bytes(data[offset:offset + 4], "little")
    return struct.pack("<I", checksum)


def _ftp_frame(command: int, payload: bytes) -> bytes:
    """Encode a file-transfer service command.

    Args:
        command: File-transfer sub-command.
        payload: Command payload.

    Returns:
        A service 0x5E wire frame.
    """
    return frame(
        bytes((0x01, 0x00, 0x5E, command))
        + struct.pack("<H", len(payload))
        + payload
    )


def file_header_frame(data: bytes) -> bytes:
    """Encode the staging file's size, checksum, and path.

    Args:
        data: Complete file bytes to transfer.

    Returns:
        The file-header wire frame.
    """
    return _ftp_frame(
        0x01,
        b"\x00" + struct.pack("<I", len(data))
        + file_checksum(data) + STAGING_PATH.encode("ascii"),
    )


def file_block_frame(offset: int, chunk: bytes) -> bytes:
    """Encode one file block at its absolute byte offset.

    Args:
        offset: Zero-based offset in the staging file.
        chunk: Bytes at that offset.

    Returns:
        The file-block wire frame.
    """
    return _ftp_frame(0x02, struct.pack("<I", offset) + chunk)


def pad_for_wire(data: bytes) -> bytes:
    """Append newlines to reproduce the hardware-proven 80/.../4 layout.

    Args:
        data: Original file bytes, without decoding or newline conversion.

    Returns:
        File bytes whose length is congruent to four modulo eighty.
    """
    return data + b"\n" * ((4 - len(data) % CHUNK_SIZE) % CHUNK_SIZE)


def blocks(data: bytes) -> Iterator[tuple[int, bytes]]:
    """Split the file exactly as the reference uploader does.

    Args:
        data: File bytes, normally padded by pad_for_wire.

    Yields:
        Pairs of file offsets and chunks, ending with the final four bytes.
    """
    if len(data) <= 4:
        yield 0, data
        return
    end = len(data) - 4
    for offset in range(0, end, CHUNK_SIZE):
        yield offset, data[offset:min(offset + CHUNK_SIZE, end)]
    yield end, data[end:]


@dataclass(frozen=True)
class Frame:
    """A validated incoming wire frame."""

    hdr: int
    body: bytes


class FrameReader:
    """Incrementally separate valid frames from program output."""

    def __init__(self, on_text: Callable[[str], None] | None = None) -> None:
        """Initialize the stream parser.

        Args:
            on_text: Optional callback for non-frame bytes, decoded as Latin-1.
        """
        self._buffer = bytearray()
        self._on_text = on_text

    def _text(self, data: bytes) -> None:
        """Deliver program output without interpreting it as protocol data.

        Args:
            data: Non-frame bytes.
        """
        if data and self._on_text is not None:
            self._on_text(data.decode("latin1"))

    def push(self, chunk: bytes) -> list[Frame]:
        """Parse incoming bytes, retaining incomplete frames for the next call.

        Args:
            chunk: The next serial read.

        Returns:
            Valid frames in arrival order. Invalid frames are treated as text.
        """
        self._buffer.extend(chunk)
        result: list[Frame] = []
        while self._buffer:
            start = self._buffer.find(bytes((F3,)))
            if start < 0:
                self._text(bytes(self._buffer))
                self._buffer.clear()
                break
            if start:
                self._text(bytes(self._buffer[:start]))
                del self._buffer[:start]
            if len(self._buffer) < 4:
                break
            low, high = self._buffer[2:4]
            length = low | high << 8
            if self._buffer[1] != (F3 + low + high) & 0xFF or length > 4096:
                self._text(bytes(self._buffer[:1]))
                del self._buffer[:1]
                continue
            if len(self._buffer) < length + 6:
                break
            body = bytes(self._buffer[4:4 + length])
            if (
                self._buffer[4 + length] != sum(body) & 0xFF
                or self._buffer[5 + length] != F4
            ):
                self._text(bytes(self._buffer[:1]))
                del self._buffer[:1]
                continue
            result.append(Frame(self._buffer[1], body))
            del self._buffer[:length + 6]
        return result


def is_ping_reply(reply: Frame) -> bool:
    """Match upload-mode replies using the reference's lenient rule.

    Args:
        reply: A validated incoming frame.

    Returns:
        Whether the frame acknowledges a ping.
    """
    return len(reply.body) == 3 and reply.body[0] == 0x0D


def is_exec_reply(reply: Frame) -> bool:
    """Match script replies using the reference's lenient rule.

    Args:
        reply: A validated incoming frame.

    Returns:
        Whether the frame acknowledges a script.
    """
    return b'"ret"' in reply.body or len(reply.body) == 18


def is_file_ack(reply: Frame) -> bool:
    """Match file acknowledgements using the reference's lenient rule.

    Args:
        reply: A validated incoming frame.

    Returns:
        Whether the frame acknowledges a file-transfer command.
    """
    return b"\x5e\xf0" in reply.body or len(reply.body) == 7
