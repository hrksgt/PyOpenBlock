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

"""Reusable synchronous CyberPi upload and serial-port lifecycle."""

import math
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .protocol import (
    EXEC_IMPORT_CONFIG,
    EXEC_REPL_OFF,
    PING,
    Frame,
    FrameReader,
    blocks,
    file_block_frame,
    file_header_frame,
    is_exec_reply,
    is_file_ack,
    is_ping_reply,
    pad_for_wire,
)


class UploadError(Exception):
    """A connection, transfer, or acknowledgement failure."""


class SerialConnection(Protocol):
    """The pySerial surface required by the uploader."""

    timeout: float | None

    @property
    def in_waiting(self) -> int:
        """Return the number of currently buffered input bytes."""
        ...

    def read(self, size: int = 1) -> bytes:
        """Read at most size bytes, subject to the configured timeout.

        Args:
            size: Maximum number of bytes.

        Returns:
            Bytes received before the timeout.
        """
        ...

    def write(self, data: bytes) -> int:
        """Write a wire frame.

        Args:
            data: Bytes to send.

        Returns:
            Number of bytes written.
        """
        ...

    def flush(self) -> None:
        """Wait for all pending output to be transmitted."""
        ...

    def close(self) -> None:
        """Release the serial port."""
        ...


@dataclass(frozen=True)
class UploadOptions:
    """Reference defaults; timing can be overridden by library callers."""

    boot_wait: float = 3.0
    ack_timeout: float = 3.0
    retries: int = 3

    def __post_init__(self) -> None:
        """Reject timing values that would prevent a valid transaction.

        Raises:
            ValueError: If a timing is non-finite or invalid, or retries is zero.
        """
        if not math.isfinite(self.boot_wait) or self.boot_wait < 0:
            raise ValueError("boot_wait must be finite and non-negative")
        if not math.isfinite(self.ack_timeout) or self.ack_timeout <= 0:
            raise ValueError("ack_timeout must be finite and positive")
        if self.retries < 1:
            raise ValueError("retries must be at least one")


@dataclass(frozen=True)
class UploadResult:
    """Completed upload statistics, including appended newline bytes."""

    bytes: int
    frames: int


def open_serial(port_path: str) -> SerialConnection:
    """Open the OS-specific serial device with the reference 115200 8N1 settings.

    Args:
        port_path: Windows COM name or a POSIX serial-device path.

    Returns:
        An open serial connection with DTR enabled.

    Raises:
        UploadError: If pySerial is unavailable or the port cannot be opened.
    """
    try:
        import serial
    except ModuleNotFoundError as error:
        if error.name != "serial":
            raise
        raise UploadError(
            "pySerial is required. From the project directory, run: "
            "python -m pip install -e ."
        ) from error

    if sys.platform == "darwin" and port_path.startswith("/dev/tty."):
        port_path = port_path.replace("/dev/tty.", "/dev/cu.", 1)
    try:
        return serial.Serial(
            port=port_path,
            baudrate=115200,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
            write_timeout=3.0,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
    except serial.SerialException as error:
        raise UploadError(
            f"Could not open {port_path}: {error}. Check the USB data cable and "
            "power, and close mBlock, mLink, or any serial monitor using the port."
        ) from error


def _transact(
    port: SerialConnection,
    reader: FrameReader,
    data: bytes,
    match: Callable[[Frame], bool],
    label: str,
    options: UploadOptions,
    log: Callable[[str], None] | None,
) -> None:
    """Send a frame and wait for its acknowledgement before continuing.

    Args:
        port: Open serial connection.
        reader: Persistent incoming stream parser.
        data: Complete outgoing wire frame.
        match: Reference acknowledgement matcher.
        label: Human-readable transaction name.
        options: Timeout and retry settings.
        log: Optional status callback.

    Raises:
        UploadError: If a write is incomplete or all reply attempts time out.
        OSError: If the serial device fails during I/O.
    """
    for attempt in range(1, options.retries + 1):
        if port.write(data) != len(data):
            raise UploadError(f"Incomplete serial write for {label}")
        port.flush()
        deadline = time.monotonic() + options.ack_timeout
        while (remaining := deadline - time.monotonic()) > 0:
            port.timeout = min(0.05, remaining)
            incoming = port.read(max(1, min(port.in_waiting, 4096)))
            replies = reader.push(incoming)
            if any(match(reply) for reply in replies):
                return
        if log is not None:
            log(f"{label}: no reply (attempt {attempt}/{options.retries})")
    raise UploadError(
        f"No reply from the CyberPi for {label}. Check that it is switched on "
        "and mBlock/mLink is not using the port, then try again."
    )


def upload(
    source: bytes,
    port_path: str,
    *,
    options: UploadOptions = UploadOptions(),
    on_progress: Callable[[int, int], None] | None = None,
    on_text: Callable[[str], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> UploadResult:
    """Upload file bytes and reboot, following the reference uploader sequence.

    Args:
        source: Original file contents; no Python or encoding validation occurs.
        port_path: Serial device to open.
        options: Reference defaults or library-level timing overrides.
        on_progress: Optional callback receiving completed and total frame counts.
        on_text: Optional callback for the board's non-frame output.
        log: Optional callback receiving status and retry messages.

    Returns:
        Wire byte and acknowledged frame counts after successful restart.

    Raises:
        UploadError: If opening, writing, or acknowledgement fails.
        OSError: If serial I/O fails. The connection is closed on all exits.
    """
    data = pad_for_wire(source)
    parts = list(blocks(data))
    total = 6 + 1 + len(parts) + 3
    done = 0
    preamble = (
        (PING, is_ping_reply, "upload-mode"),
        (EXEC_IMPORT_CONFIG, is_exec_reply, "prepare import config"),
        (EXEC_REPL_OFF, is_exec_reply, "prepare repl off"),
    )
    reader = FrameReader(on_text)
    port = open_serial(port_path)
    try:
        if log is not None:
            log(f"Waiting {options.boot_wait:g}s for the CyberPi to boot...")
        time.sleep(options.boot_wait)
        # The reference reader discards boot-time frames without active waiters.
        pending = port.in_waiting
        while pending:
            incoming = port.read(min(pending, 4096))
            if not incoming:
                break
            pending -= len(incoming)
            reader.push(incoming)

        def send(wire: bytes, matcher: Callable[[Frame], bool], label: str) -> None:
            """Complete a transaction and notify library progress listeners.

            Args:
                wire: Outgoing frame.
                matcher: Expected reply matcher.
                label: Transaction name used in errors and logs.
            """
            nonlocal done
            _transact(port, reader, wire, matcher, label, options, log)
            done += 1
            if on_progress is not None:
                on_progress(done, total)

        for pass_number in (1, 2):
            for wire, matcher, label in preamble:
                send(wire, matcher, f"{label} ({pass_number})")
        send(file_header_frame(data), is_file_ack, "file header")
        for offset, chunk in parts:
            send(file_block_frame(offset, chunk), is_file_ack, f"block @{offset}")
        for wire, matcher, label in preamble:
            send(wire, matcher, f"{label} (finish)")
    finally:
        port.close()

    if log is not None:
        log("Restarting the CyberPi to run the uploaded program...")
    # Match Board.restart(): close, wait 300 ms, reopen to toggle DTR.
    time.sleep(0.3)
    restarted_port = open_serial(port_path)
    try:
        time.sleep(0.5)
    finally:
        restarted_port.close()
    return UploadResult(bytes=len(data), frames=done)
