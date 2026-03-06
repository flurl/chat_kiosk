#!/usr/bin/env python3
"""GPIO Virtual Joystick Daemon

Reads physical pushbuttons wired to GPIO pins and emits events to a uinput
virtual joystick device. The kernel's joydev module exposes /dev/input/js0,
which Kivy's SDL2 backend detects automatically.

Prerequisites:
    # System packages (required to build lgpio)
    sudo apt-get install -y swig liblgpio-dev

    # Kernel modules
    sudo modprobe uinput
    # (add to /etc/modules for persistence)

    # udev rule for non-root /dev/uinput access (create once)
    echo 'SUBSYSTEM=="misc", KERNEL=="uinput", GROUP="input", MODE="0660"' \
        | sudo tee /etc/udev/rules.d/99-uinput.rules
    sudo udevadm control --reload-rules && sudo udevadm trigger

    # Python packages
    pip install lgpio gpiozero python-uinput
"""

import signal
import sys
import uinput
from gpiozero import Button, RotaryEncoder

# ---------------------------------------------------------------------------
# GPIO pin mapping (BCM numbering)
# ---------------------------------------------------------------------------
PIN_ENC_CLK = 17   # Rotary encoder CLK (A) — hat UP / DOWN
PIN_ENC_DT  = 27   # Rotary encoder DT  (B)
PIN_LEFT    = 22
PIN_RIGHT   = 23
PIN_A       = 24   # BTN_SOUTH (SDL button 0)
PIN_B       = 25   # BTN_EAST  (SDL button 1)
PIN_C       = 4    # BTN_WEST  (SDL button 2)

BOUNCE_TIME = 0.020  # 20 ms debounce

# ---------------------------------------------------------------------------
# uinput device
# ---------------------------------------------------------------------------
CAPABILITIES = (
    uinput.ABS_HAT0X + (-1, 1, 0, 0),   # left=-1, center=0, right=+1
    uinput.ABS_HAT0Y + (-1, 1, 0, 0),   # up=-1,   center=0, down=+1
    uinput.BTN_SOUTH,
    uinput.BTN_EAST,
    uinput.BTN_WEST,
)


def main():
    with uinput.Device(CAPABILITIES, name="gpio-joystick") as device:

        # ------------------------------------------------------------------
        # Rotary encoder → hat Y axis (momentary pulse per detent)
        # ------------------------------------------------------------------
        def hat_y_pulse(value):
            device.emit(uinput.ABS_HAT0Y, value)
            device.emit(uinput.ABS_HAT0Y, 0)

        encoder = RotaryEncoder(PIN_ENC_CLK, PIN_ENC_DT, bounce_time=BOUNCE_TIME)
        encoder.when_rotated_clockwise         = lambda: hat_y_pulse(1)   # DOWN
        encoder.when_rotated_counter_clockwise = lambda: hat_y_pulse(-1)  # UP

        # ------------------------------------------------------------------
        # Hat X axis buttons and action buttons (active-low: pin to GND)
        # ------------------------------------------------------------------
        def emit_hat_x():
            val = 0
            if btn_left.is_pressed and not btn_right.is_pressed:
                val = -1
            elif btn_right.is_pressed and not btn_left.is_pressed:
                val = 1
            device.emit(uinput.ABS_HAT0X, val)

        btn_left  = Button(PIN_LEFT,  pull_up=True, bounce_time=BOUNCE_TIME)
        btn_right = Button(PIN_RIGHT, pull_up=True, bounce_time=BOUNCE_TIME)
        btn_a     = Button(PIN_A,     pull_up=True, bounce_time=BOUNCE_TIME)
        btn_b     = Button(PIN_B,     pull_up=True, bounce_time=BOUNCE_TIME)
        btn_c     = Button(PIN_C,     pull_up=True, bounce_time=BOUNCE_TIME)

        btn_left.when_pressed    = emit_hat_x
        btn_left.when_released   = emit_hat_x
        btn_right.when_pressed   = emit_hat_x
        btn_right.when_released  = emit_hat_x

        btn_a.when_pressed  = lambda: device.emit(uinput.BTN_SOUTH, 1)
        btn_a.when_released = lambda: device.emit(uinput.BTN_SOUTH, 0)

        btn_b.when_pressed  = lambda: device.emit(uinput.BTN_EAST, 1)
        btn_b.when_released = lambda: device.emit(uinput.BTN_EAST, 0)

        btn_c.when_pressed  = lambda: device.emit(uinput.BTN_WEST, 1)
        btn_c.when_released = lambda: device.emit(uinput.BTN_WEST, 0)

        # ------------------------------------------------------------------
        # Clean shutdown
        # ------------------------------------------------------------------
        def shutdown(signum, frame):
            device.emit(uinput.ABS_HAT0X, 0)
            device.emit(uinput.ABS_HAT0Y, 0)
            device.emit(uinput.BTN_SOUTH, 0)
            device.emit(uinput.BTN_EAST,  0)
            device.emit(uinput.BTN_WEST,  0)
            sys.exit(0)

        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT,  shutdown)

        print("gpio-joystick: running — /dev/input/js0 should be available")
        signal.pause()


if __name__ == "__main__":
    main()
