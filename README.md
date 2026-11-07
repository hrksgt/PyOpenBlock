# PyOpenBlock

An open-source Python command-line uploader for CyberPi and mBot2.

## Requirements

- Python 3.15 or later.
- A powered-on CyberPi connected with a USB **data** cable.
- Windows and a working USB serial driver (CyberPi commonly uses a CH340).
- Close mBlock, mLink, and serial monitors before uploading.

Install PyOpenBlock and its serial dependency from the project directory:

```powershell
python -m pip install -e .
```

This editable installation makes the package under `src` importable and
reflects source changes without reinstalling. Dependencies and package metadata
are managed in [pyproject.toml](pyproject.toml)

## Usage

```powershell
python pyOpenBlock.py -p COM5 myapp.py
python pyOpenBlock.py -p COM5 "C:\path with spaces\myapp.py"
python pyOpenBlock.py --help
```

After installation, the `pyopenblock` command is also available from any
directory:

```powershell
pyopenblock -p COM5 myapp.py
```

`-p` (or `--port`) is required. Find the COM number in Windows Device Manager
under **Ports (COM & LPT)**. Exactly one positional file is accepted. Its suffix,
encoding, and Python syntax are not checked; its bytes are read unchanged.
The file is not executed on your computer.

The uploader waits three seconds for the board to boot, transfers the file,
restarts the board, and closes the port. It appends trailing newline bytes to
match the reference transfer layout; it does not change the local file.
Exit codes are `0` for success, `1` for file/communication errors, `2` for
invalid CLI arguments, and `130` for Ctrl+C.

CyberPi programs should normally use an event handler:

```python
import event, cyberpi

@event.start
def on_start():
    cyberpi.display.show_label("Hello!", 24, "center")
```

Bare top-level loops may prevent later uploads until the board is power-cycled.
No event-handler validation is performed.

There is a CyberPi example at [examples/CyberPi_sample.py](examples/CyberPi_sample.py).
It displays a greeting, lights the LEDs green, and displays `Button A` or
`Button B` when the corresponding button is pressed:

```powershell
python pyOpenBlock.py -p COM5 examples/CyberPi_sample.py
```

## Transfer protocol

The implementation ports the protocol and default upload sequence
from [weare2plus3/cyberpi-uploader](https://github.com/weare2plus3/cyberpi-uploader).

The upload sequence is:

1. Open at **115200 baud, 8N1**, with no flow control, and wait three seconds.
2. Send upload-mode ping, `import config`, and
   `config.write_config("repl_enable", False)`, twice.
3. Send the header for `/flash/_xx_main.py` with the padded byte count and the
   XOR of little-endian 32-bit words (zero-padded for the checksum).
4. Send 80-byte blocks followed by a final four-byte block. Newline padding
   makes the total size congruent to four modulo eighty, including empty files.
5. Send the same ping/config sequence once more.
6. Close the port, wait 300 ms, reopen to toggle DTR, wait 500 ms, and close.
   The firmware activates the staging file on reboot.

Each frame uses `F3 hdr len_lo len_hi body... ck F4`. The header checksum is
`(0xF3 + len_lo + len_hi) & 0xFF`; the body checksum is `sum(body) & 0xFF`.
Each transaction waits up to three seconds for its reply, with three attempts
in total. The incremental reader and lenient reply matchers mirror the
reference, including separating program output from binary acknowledgements.

If the port cannot open, check the COM number, cable, driver, and competing
applications. If acknowledgements time out, check board power and power-cycle
it before retrying. If a transferred program does not start, power-cycle it.

## Project structure

```text
PyOpenBlock\
|-- pyOpenBlock.py
|-- pyproject.toml
|-- src\
|   `-- pyopenblock\
|       |-- __init__.py
|       |-- cli.py
|       |-- protocol.py
|       `-- uploader.py
|-- tests\
|-- examples\
|   `-- CyberPi_sample.py
`-- README.md
```

## Reuse and future platforms

[pyOpenBlock.py](pyOpenBlock.py) is a thin compatibility entry point that
delegates to [cli.py](src/pyopenblock/cli.py).
[protocol.py](src/pyopenblock/protocol.py) contains OS-independent encoding and
parsing. [uploader.py](src/pyopenblock/uploader.py) provides the transfer and
serial-port lifecycle. The installed package exposes the public library API:

```python
from pyopenblock import upload

result = upload(b"import event, cyberpi\n", "COM5")
```

`upload(source: bytes, port_path: str)` accepts optional progress, text, and log
callbacks, so a future VS Code extension can reuse the transfer implementation.
The `src` layout uses normal package imports, without modifying `sys.path`.

Serial access is isolated in `open_serial()` and uses cross-platform pySerial.
No Windows-only serial API or COM-number validation is imposed. POSIX device
paths can be passed through `-p`; macOS `/dev/tty.*` paths are normalized to
`/dev/cu.*` as in the reference. **Linux and macOS hardware support has not
been validated yet.**

## Tests

```powershell
python -m pip install -e .
python -m unittest discover -s tests -v
```

## License and attribution

MIT; see [LICENSE](LICENSE). The protocol and sequence are derived from
[weare2plus3/cyberpi-uploader](https://github.com/weare2plus3/cyberpi-uploader),
copyright (c) 2026 TWO PLUS THREE LIMITED, under its
[MIT license](https://github.com/weare2plus3/cyberpi-uploader/blob/main/LICENSE).
Derived Python modules retain that copyright and permission notice.
