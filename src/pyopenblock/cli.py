# MIT License
#
# Copyright (c) 2026 hrksgt
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

"""Command-line Python file uploader for CyberPi and mBot2."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .uploader import UploadError, upload


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the CLI, upload exactly one file, and report the outcome.

    Args:
        argv: Arguments without the executable name; None uses sys.argv.

    Returns:
        Zero on success, one on file/serial failure, or 130 on interruption.
        argparse exits with two for invalid command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Upload one file to a CyberPi over USB and restart the board.",
        usage="%(prog)s -p PORT file",
    )
    parser.add_argument("-p", "--port", required=True, help="Serial port, e.g. COM5")
    parser.add_argument("file", type=Path, help="One file to upload (no type validation)")
    args = parser.parse_args(argv)
    try:
        source = args.file.read_bytes()
        result = upload(source, args.port, log=print)
    except (OSError, UploadError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Upload interrupted.", file=sys.stderr)
        return 130
    print(
        f"Uploaded {args.file.name} to {args.port}: "
        f"{result.bytes} bytes, {result.frames} acknowledged frames. "
        "CyberPi restarted. If the new program does not start, power-cycle it."
    )
    return 0
