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

"""CyberPi display and button example; upload rather than run on the host."""

import event, cyberpi

@event.start
def on_start():
    """Display a greeting and turn the LEDs green on startup."""
    cyberpi.display.show_label("Hello World!", 10, "center")
    cyberpi.led.on(0, 255, 0)


@event.is_press("a")
def on_button_a():
    """Display a message when button A is pressed."""
    cyberpi.display.show_label("Button A", 10, "center")


@event.is_press("b")
def on_button_b():
    """Display a message when button B is pressed."""
    cyberpi.display.show_label("Button B", 10, "center")
