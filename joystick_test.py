#!/usr/bin/env python3
"""Simple Kivy joystick input test.

Run gpio_joystick.py first, then:
    python3 joystick_test.py
"""

from kivy.app import App
from kivy.core.window import Window
from kivy.uix.label import Label


class JoystickTestApp(App):
    def build(self):
        self.label = Label(
            text="Waiting for joystick input...\n\nPress buttons or move hat.",
            font_size="24sp",
            halign="center",
        )

        Window.bind(on_joy_hat=self.on_hat)
        Window.bind(on_joy_button_down=self.on_button_down)
        Window.bind(on_joy_button_up=self.on_button_up)

        return self.label

    def on_hat(self, window, stickid, hatid, value):
        self.label.text = f"HAT\nstick={stickid}  hat={hatid}  value={value}"
        print(f"hat: stick={stickid} hat={hatid} value={value}")

    def on_button_down(self, window, stickid, buttonid):
        self.label.text = f"BUTTON DOWN\nstick={stickid}  button={buttonid}"
        print(f"button down: stick={stickid} button={buttonid}")

    def on_button_up(self, window, stickid, buttonid):
        self.label.text = f"BUTTON UP\nstick={stickid}  button={buttonid}"
        print(f"button up: stick={stickid} button={buttonid}")


if __name__ == "__main__":
    JoystickTestApp().run()
