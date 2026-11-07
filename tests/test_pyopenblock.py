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

"""Wire protocol, project-owned sample transfers, and CLI tests."""

import contextlib
import io
import runpy
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

import pyOpenBlock
from pyopenblock import cli, protocol, uploader

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_FILES = (
    ROOT / "tests" / "fixtures" / "sample_program.py",
    ROOT / "examples" / "test.py",
)
PREAMBLE = (
    bytes.fromhex("f3f603000d00000df4"),
    bytes.fromhex(
        "f3202d002804000027007472793a0a20202020696d706f727420636f6e666967"
        "0a6578636570743a0a2020202070617373b5f4"
    ),
    bytes.fromhex(
        "f33c49002804000043007472793a0a20202020636f6e6669672e7772697465"
        "5f636f6e66696728227265706c5f656e61626c65222c2046616c7365290a"
        "6578636570743a0a202020207061737389f4"
    ),
)
OPTIONS = uploader.UploadOptions(boot_wait=0, ack_timeout=0.01, retries=3)


def reply_frame(body: bytes) -> bytes:
    """Encode mock replies independently of the implementation under test.

    Args:
        body: Mock response body.

    Returns:
        A complete serial response frame.
    """
    length = struct.pack("<H", len(body))
    return (
        bytes((0xF3, (0xF3 + sum(length)) & 255))
        + length + body + bytes((sum(body) & 255, 0xF4))
    )


def expected_transfer(source: bytes) -> tuple[bytes, list[bytes]]:
    """Build expected file frames without using the production encoder.

    Args:
        source: Original bytes read from a project-owned sample file.

    Returns:
        Independently padded file bytes and expected file-transfer frames.
    """
    size = 4
    while size < len(source):
        size += 80
    data = source + b"\n" * (size - len(source))
    checksum = 0
    for (word,) in struct.iter_unpack("<I", data):
        checksum ^= word

    def file_command(command: int, payload: bytes) -> bytes:
        """Encode a test file command using only standard-library primitives.

        Args:
            command: Header or block sub-command.
            payload: Expected command payload.

        Returns:
            The expected wire frame.
        """
        return reply_frame(
            struct.pack("<BB BB H", 1, 0, 0x5E, command, len(payload)) + payload
        )

    header = struct.pack("<BII", 0, size, checksum) + b"/flash/_xx_main.py"
    frames = [file_command(1, header)]
    for offset in range(0, size - 4, 80):
        frames.append(file_command(2, struct.pack("<I", offset) + data[offset:offset + 80]))
    frames.append(file_command(2, struct.pack("<I", size - 4) + data[-4:]))
    return data, frames


class FakeSerial:
    """Strict wire validator and staging-file receiver with fragmented replies."""

    def __init__(
        self, *, silent: bool = False, drop_block: bool = False,
        short_write: bool = False, read_error: bool = False,
    ) -> None:
        """Initialize the mock board and failure modes.

        Args:
            silent: Never acknowledge requests.
            drop_block: Lose the first block's ACK after accepting its bytes.
            short_write: Report an incomplete write.
            read_error: Raise a serial read failure.
        """
        self.timeout: float | None = 0.05
        self.buffer = bytearray(b"PYB: fast reboot\r\n" + reply_frame(b"\x0d\x00\x00"))
        self.closed = False
        self.silent = silent
        self.drop_block = drop_block
        self.short_write = short_write
        self.read_error = read_error
        self.writes: list[bytes] = []
        self.data = bytearray()
        self.path = ""
        self.size = 0
        self.checksum = b""
        self.flushes = 0
        self.block_writes = 0

    @property
    def in_waiting(self) -> int:
        """Return the buffered mock input length."""
        return len(self.buffer)

    def read(self, size: int = 1) -> bytes:
        """Return fragmented serial input, or inject a read failure.

        Args:
            size: Maximum requested read length.

        Returns:
            At most two input bytes.

        Raises:
            OSError: When read_error is enabled.
        """
        if self.read_error:
            raise OSError("Device disconnected")
        count = min(size, 2)
        chunk = bytes(self.buffer[:count])
        del self.buffer[:count]
        return chunk

    def write(self, data: bytes) -> int:
        """Validate a request, emulate the board, and queue its response.

        Args:
            data: Complete outgoing frame.

        Returns:
            The byte count, or a deliberately short count.
        """
        self.writes.append(data)
        if self.short_write:
            return len(data) - 1
        body = data[4:-2]
        assert data[0] == 0xF3 and data[-1] == 0xF4
        assert struct.unpack("<H", data[2:4])[0] == len(body)
        assert data[1] == (0xF3 + data[2] + data[3]) & 255
        assert data[-2] == sum(body) & 255
        if self.silent:
            return len(data)
        if body == b"\x0d\x00\x00":
            response = reply_frame(body)
        elif body[0] == 0x28:
            assert struct.unpack("<H", body[4:6])[0] == len(body[6:])
            response = reply_frame(b'\x28\x05\x00\x00\x0c\x00{"ret":null}')
        else:
            assert body[:3] == b"\x01\x00\x5e"
            payload = body[6:]
            assert struct.unpack("<H", body[4:6])[0] == len(payload)
            if body[3] == 1:
                assert payload[0] == 0
                self.size = struct.unpack("<I", payload[1:5])[0]
                self.checksum = payload[5:9]
                self.path = payload[9:].decode("ascii")
            else:
                assert body[3] == 2
                offset = struct.unpack("<I", payload[:4])[0]
                assert offset <= len(self.data)
                self.data[offset:offset + len(payload[4:])] = payload[4:]
                self.block_writes += 1
                if self.drop_block:
                    self.drop_block = False
                    return len(data)
            response = reply_frame(b"\x01\x00\x5e\xf0\x01\x00\x00")
        self.buffer.extend(b"program output\r\n" + response)
        return len(data)

    def flush(self) -> None:
        """Record that outgoing bytes have been drained."""
        self.flushes += 1

    def close(self) -> None:
        """Record the release of the mock port."""
        self.closed = True


class ProtocolTests(unittest.TestCase):
    """Check known wire answers and stream handling."""

    def test_sample_wire_bytes(self) -> None:
        """Match fixed preamble answers and independently encoded sample files."""
        self.assertEqual(
            (protocol.PING, protocol.EXEC_IMPORT_CONFIG, protocol.EXEC_REPL_OFF),
            PREAMBLE,
        )
        for file in SAMPLE_FILES:
            with self.subTest(file=file.name):
                source = file.read_bytes()
                expected_data, expected_frames = expected_transfer(source)
                data = protocol.pad_for_wire(source)
                self.assertEqual(data, expected_data)
                actual = [protocol.file_header_frame(data)]
                actual.extend(
                    protocol.file_block_frame(offset, chunk)
                    for offset, chunk in protocol.blocks(data)
                )
                self.assertEqual(actual, expected_frames)

    def test_padding_and_block_boundaries(self) -> None:
        """Check empty files and all block residues through several blocks."""
        for size in range(401):
            with self.subTest(size=size):
                source = b"A" * size
                padded = protocol.pad_for_wire(source)
                parts = list(protocol.blocks(padded))
                self.assertEqual(len(padded) % 80, 4)
                self.assertTrue(padded.startswith(source))
                self.assertEqual(padded[size:], b"\n" * (len(padded) - size))
                self.assertEqual(len(parts[-1][1]), 4)
                self.assertTrue(all(len(chunk) == 80 for _, chunk in parts[:-1]))
                self.assertEqual(b"".join(chunk for _, chunk in parts), padded)
                self.assertEqual([offset for offset, _ in parts], list(range(0, len(padded), 80)))
        for size in range(5):
            self.assertEqual(list(protocol.blocks(b"A" * size)), [(0, b"A" * size)])

    def test_checksums_and_length_limits(self) -> None:
        """Verify little-endian XOR and multi-byte frame header lengths."""
        self.assertEqual(protocol.file_checksum(bytes((1, 2, 3, 4, 5))), b"\x04\x02\x03\x04")
        self.assertEqual(protocol.file_checksum(b"\xff\0\0\0" * 2), b"\0" * 4)
        for size in (0, 3, 7, 300, 4096, 65535):
            wire = protocol.frame(b"\x01" * size)
            self.assertEqual(wire[1], (0xF3 + (size & 255) + (size >> 8)) & 255)
            self.assertEqual(struct.unpack("<H", wire[2:4])[0], size)
        with self.assertRaises(ValueError):
            protocol.frame(b"\0" * 65536)

    def test_fragmented_frames_and_corrupt_input(self) -> None:
        """Recover from noise, bad checksums, oversized headers, and fragments."""
        bad_header = bytearray(protocol.PING)
        bad_header[1] ^= 1
        bad_body = bytearray(protocol.PING)
        bad_body[-2] ^= 1
        bad_end = bytearray(protocol.PING)
        bad_end[-1] = 0
        oversized_header = bytes((0xF3, (0xF3 + 1 + 16) & 255, 1, 16))
        text: list[str] = []
        reader = protocol.FrameReader(text.append)
        stream = (
            b"boot\r\n" + bad_header + bad_body + bad_end + oversized_header
            + b"hi \xf3 there\n" + protocol.PING + protocol.EXEC_REPL_OFF + b"bye"
        )
        received: list[protocol.Frame] = []
        for byte in stream:
            received.extend(reader.push(bytes((byte,))))
        self.assertEqual([item.body for item in received], [protocol.PING[4:-2], protocol.EXEC_REPL_OFF[4:-2]])
        self.assertTrue("".join(text).startswith("boot\r\n"))
        self.assertTrue("".join(text).endswith("bye"))
        self.assertEqual(len(protocol.FrameReader().push(protocol.PING * 2)), 2)

    def test_reference_reply_matchers(self) -> None:
        """Preserve both content-based and length-based ACK matching."""
        self.assertTrue(protocol.is_ping_reply(protocol.Frame(0, b"\x0d\x01\x02")))
        self.assertFalse(protocol.is_ping_reply(protocol.Frame(0, b"")))
        self.assertTrue(protocol.is_exec_reply(protocol.Frame(0, b'{"ret":1}')))
        self.assertTrue(protocol.is_exec_reply(protocol.Frame(0, b"x" * 18)))
        self.assertTrue(protocol.is_file_ack(protocol.Frame(0, b"\x5e\xf0")))
        self.assertTrue(protocol.is_file_ack(protocol.Frame(0, b"x" * 7)))
        self.assertFalse(protocol.is_exec_reply(protocol.Frame(0, b"noise")))
        self.assertFalse(protocol.is_file_ack(protocol.Frame(0, b"noise")))


class UploadTests(unittest.TestCase):
    """Exercise transfer order, retries, lifecycle, and platform settings."""

    def test_complete_upload_and_restart(self) -> None:
        """Compare received bytes with project-owned files and verify reset order."""
        for file in SAMPLE_FILES:
            with self.subTest(file=file.name):
                source = file.read_bytes()
                expected_data, expected_frames = expected_transfer(source)
                board, restarted = FakeSerial(), FakeSerial()
                progress: list[tuple[int, int]] = []
                text: list[str] = []
                with patch.object(uploader, "open_serial", side_effect=[board, restarted]) as opened, patch.object(uploader.time, "sleep") as sleep:
                    result = uploader.upload(
                        source, "COM5", options=OPTIONS,
                        on_progress=lambda done, total: progress.append((done, total)),
                        on_text=text.append,
                    )
                    self.assertEqual(opened.call_args_list[0].args, ("COM5",))
                    self.assertEqual(opened.call_count, 2)
                    self.assertEqual([call.args[0] for call in sleep.call_args_list], [0, 0.3, 0.5])
                self.assertEqual(
                    board.writes, list(PREAMBLE) * 2 + expected_frames + list(PREAMBLE)
                )
                data = bytes(board.data)
                self.assertEqual(data, expected_data)
                self.assertEqual(data[:len(source)], source)
                self.assertEqual(data[len(source):], b"\n" * (len(data) - len(source)))
                self.assertEqual(file.read_bytes(), source)
                self.assertEqual(board.path, "/flash/_xx_main.py")
                self.assertEqual(board.size, len(data))
                checksum = 0
                for offset in range(0, len(data), 4):
                    checksum ^= struct.unpack("<I", data[offset:offset + 4])[0]
                self.assertEqual(board.checksum, struct.pack("<I", checksum))
                self.assertEqual(result.bytes, len(data))
                self.assertEqual(result.frames, len(board.writes))
                self.assertEqual(board.flushes, len(board.writes))
                self.assertEqual(progress, [(done, result.frames) for done in range(1, result.frames + 1)])
                self.assertIn("program output", "".join(text))
                self.assertTrue(board.closed and restarted.closed)
                self.assertEqual(restarted.writes, [])

    def test_dropped_ack_retries_identical_block(self) -> None:
        """Resend the same offset/data without inflating acknowledged progress."""
        board, restarted = FakeSerial(drop_block=True), FakeSerial()
        logs: list[str] = []
        with patch.object(uploader, "open_serial", side_effect=[board, restarted]), patch.object(uploader.time, "sleep"):
            result = uploader.upload(b"print('hello')\r\n", "COM5", options=OPTIONS, log=logs.append)
        self.assertEqual(board.writes[7], board.writes[8])
        self.assertEqual(len(board.writes), result.frames + 1)
        self.assertEqual(bytes(board.data), protocol.pad_for_wire(b"print('hello')\r\n"))
        self.assertTrue(any("attempt 1/3" in line for line in logs))

    def test_transfer_failures_close_without_restart(self) -> None:
        """Release the port on timeout, partial write, disconnection, and Ctrl+C."""
        for failure in ("silent", "short_write", "read_error", "interrupt"):
            with self.subTest(failure=failure):
                board = FakeSerial(**({failure: True} if failure != "interrupt" else {}))
                with patch.object(uploader, "open_serial", return_value=board) as opened, patch.object(uploader.time, "sleep") as sleep:
                    if failure == "interrupt":
                        sleep.side_effect = KeyboardInterrupt
                    error_type = KeyboardInterrupt if failure == "interrupt" else OSError if failure == "read_error" else uploader.UploadError
                    with self.assertRaises(error_type):
                        uploader.upload(b"file", "COM5", options=OPTIONS)
                    self.assertTrue(board.closed)
                    self.assertEqual(opened.call_count, 1)
                    if failure == "silent":
                        self.assertEqual(board.writes, [protocol.PING] * 3)

    def test_restart_failure_is_not_success(self) -> None:
        """Propagate a failed reopen rather than claiming activation succeeded."""
        board = FakeSerial()
        with patch.object(uploader, "open_serial", side_effect=[board, uploader.UploadError("Reopen failed")]), patch.object(uploader.time, "sleep"):
            with self.assertRaisesRegex(uploader.UploadError, "Reopen failed"):
                uploader.upload(b"", "COM5", options=OPTIONS)
        self.assertTrue(board.closed)
        self.assertEqual(bytes(board.data), b"\n" * 4)

    def test_serial_settings_and_platform_paths(self) -> None:
        """Use pySerial on all platforms and normalize only macOS dial-in paths."""
        import serial

        for platform, path, normalized in (
            ("win32", "COM5", "COM5"),
            ("linux", "/dev/ttyUSB0", "/dev/ttyUSB0"),
            ("darwin", "/dev/tty.usbserial-110", "/dev/cu.usbserial-110"),
        ):
            with self.subTest(platform=platform), patch.object(uploader.sys, "platform", platform), patch.object(serial, "Serial", return_value=FakeSerial()) as constructor:
                uploader.open_serial(path)
                settings = constructor.call_args.kwargs
                self.assertEqual(settings["port"], normalized)
                self.assertEqual((settings["baudrate"], settings["bytesize"], settings["parity"], settings["stopbits"]), (115200, 8, "N", 1))
                self.assertFalse(settings["xonxoff"] or settings["rtscts"] or settings["dsrdtr"])
        with patch.object(serial, "Serial", side_effect=serial.SerialException("Access denied")):
            with self.assertRaisesRegex(uploader.UploadError, "mBlock"):
                uploader.open_serial("COM5")

    def test_missing_dependency_and_invalid_options(self) -> None:
        """Report installation guidance and reject unusable timeout settings."""
        with patch.dict(sys.modules, {"serial": None}):
            with self.assertRaisesRegex(uploader.UploadError, r"python -m pip install -e \."):
                uploader.open_serial("COM5")
        for overrides in (
            {"boot_wait": -1}, {"boot_wait": float("inf")},
            {"ack_timeout": 0}, {"ack_timeout": float("nan")}, {"retries": 0},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                uploader.UploadOptions(**overrides)


class CliTests(unittest.TestCase):
    """Verify the exact requested command shape and observable failures."""

    def test_arbitrary_file_bytes_and_spaces(self) -> None:
        """Upload a single non-Python file without content or suffix checks."""
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "not python.data"
            file.write_bytes(b"\xff\x00\r\n")
            with patch.object(cli, "upload", return_value=uploader.UploadResult(4, 11)) as upload, contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(cli.main(["-p", "COM5", str(file)]), 0)
            self.assertEqual(upload.call_args.args, (b"\xff\x00\r\n", "COM5"))
            self.assertIn("4 bytes", output.getvalue())

    def test_cli_to_simulated_board(self) -> None:
        """Run the complete CLI-to-transfer pipeline with the default settings."""
        board, restarted = FakeSerial(), FakeSerial()
        connections = iter((board, restarted))

        def open_connection(port_path: str) -> FakeSerial:
            """Verify port release before the restart connection is opened.

            Args:
                port_path: Requested serial device.

            Returns:
                The next mock connection.
            """
            self.assertEqual(port_path, "COM5")
            connection = next(connections)
            if connection is restarted:
                self.assertTrue(board.closed)
            return connection

        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "myapp.py"
            source = b"import event, cyberpi\r\n"
            file.write_bytes(source)
            with (
                patch.object(uploader, "open_serial", side_effect=open_connection),
                patch.object(uploader.time, "sleep") as sleep,
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(pyOpenBlock.main(["-p", "COM5", str(file)]), 0)
            self.assertEqual(
                [call.args[0] for call in sleep.call_args_list], [3.0, 0.3, 0.5]
            )
            self.assertEqual(bytes(board.data), protocol.pad_for_wire(source))
            self.assertTrue(board.closed and restarted.closed)
            self.assertIn("CyberPi restarted", output.getvalue())

    def test_argument_count_and_required_port(self) -> None:
        """Reject missing ports, missing files, and multiple positional files."""
        for arguments in ([], ["app.py"], ["-p", "COM5"], ["-p", "COM5", "one.py", "two.py"]):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                cli.main(arguments)
            self.assertEqual(error.exception.code, 2)

    def test_file_and_upload_errors(self) -> None:
        """Report failures to stderr and return nonzero without success output."""
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "app.py"
            with patch.object(cli, "upload") as upload, contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(cli.main(["-p", "COM5", str(file)]), 1)
                upload.assert_not_called()
                self.assertIn("Error:", error.getvalue())
            file.write_bytes(b"")
            for exception, code in ((uploader.UploadError("No reply"), 1), (OSError("Disconnected"), 1), (KeyboardInterrupt(), 130)):
                with self.subTest(exception=exception), patch.object(cli, "upload", side_effect=exception), contextlib.redirect_stderr(io.StringIO()) as error, contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(cli.main(["-p", "COM5", str(file)]), code)
                    self.assertTrue(error.getvalue())
                    self.assertNotIn("Uploaded", output.getvalue())

    def test_help_from_real_entrypoint(self) -> None:
        """Run the original script from outside the repository."""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(ROOT / "pyOpenBlock.py"), "--help"],
                cwd=directory, capture_output=True, text=True, check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("-p PORT file", result.stdout)

    def test_console_entrypoint(self) -> None:
        """Run the installed console command from outside the repository."""
        executable = Path(sys.executable).parent / (
            "pyopenblock.exe" if sys.platform == "win32" else "pyopenblock"
        )
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [str(executable), "--help"], cwd=directory,
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("-p PORT file", result.stdout)

    def test_uninstalled_package_guidance(self) -> None:
        """Report setup instructions when site-packages are unavailable."""
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(ROOT / "pyOpenBlock.py"), "--help"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("python -m pip install -e .", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class ExampleTests(unittest.TestCase):
    """Verify the relocated CyberPi example without physical hardware."""

    def test_start_and_button_handlers(self) -> None:
        """Preserve the greeting, LEDs, and both user-defined button handlers."""
        event = ModuleType("event")
        cyberpi = ModuleType("cyberpi")
        with (
            patch.object(event, "start", Mock(side_effect=lambda function: function), create=True),
            patch.object(event, "is_press", Mock(side_effect=lambda button: lambda function: function), create=True) as is_press,
            patch.object(cyberpi, "display", Mock(), create=True) as display,
            patch.object(cyberpi, "led", Mock(), create=True) as led,
            patch.dict(sys.modules, {"event": event, "cyberpi": cyberpi}),
        ):
            handlers = runpy.run_path(str(ROOT / "examples" / "test.py"))
            self.assertEqual([call.args for call in is_press.call_args_list], [("a",), ("b",)])
            handlers["on_start"]()
            display.show_label.assert_called_once_with("Hello World", 10, "center")
            led.on.assert_called_once_with(0, 255, 0)
            for button in ("a", "b"):
                display.reset_mock()
                handlers[f"on_button_{button}"]()
                display.show_label.assert_called_once_with(f"Button {button.upper()}", 10, "center")


if __name__ == "__main__":
    unittest.main()
