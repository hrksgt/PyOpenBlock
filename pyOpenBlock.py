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

"""Compatibility entry point for the installed PyOpenBlock package."""

import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Forward the original script invocation to the installed package CLI.

    Args:
        argv: Arguments without the executable name; None uses sys.argv.

    Returns:
        The CLI exit code, or one if the package has not been installed.
    """
    try:
        from pyopenblock.cli import main as cli_main
    except ModuleNotFoundError as error:
        if error.name != "pyopenblock":
            raise
        print(
            "Error: PyOpenBlock is not installed. From the project directory, "
            "run: python -m pip install -e .",
            file=sys.stderr,
        )
        return 1
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
