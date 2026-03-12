"""
Touchscreen test app — draw on canvas, visualize touch state.
Run: .venv/bin/python touch_test.py
"""
from kivy.app import App
from kivy.uix.widget import Widget
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.graphics import Color, Ellipse, Line
from kivy.clock import Clock


STATUS_COLORS = {
    "idle":      (0.3, 0.3, 0.3, 1),
    "down":      (0.2, 0.8, 0.2, 1),
    "move":      (0.2, 0.6, 1.0, 1),
    "up":        (1.0, 0.5, 0.1, 1),
}

TOUCH_DOT_RADIUS = 24


class DrawCanvas(Widget):
    def __init__(self, status_label, **kw):
        super().__init__(**kw)
        self.status_label = status_label
        self._active_lines = {}   # touch.uid → points list
        self._status = "idle"
        self._active_count = 0

    def _set_status(self, state, touch=None):
        self._status = state
        color = STATUS_COLORS[state]
        if touch:
            msg = f"{state.upper()}  id={touch.uid}  ({touch.x:.0f}, {touch.y:.0f})"
        else:
            msg = "IDLE"
        self.status_label.text = msg
        self.status_label.color = color

    def on_touch_down(self, touch):
        self._active_count += 1
        self._set_status("down", touch)
        with self.canvas:
            Color(0.2, 0.8, 0.2, 0.6)
            d = TOUCH_DOT_RADIUS * 2
            Ellipse(pos=(touch.x - TOUCH_DOT_RADIUS, touch.y - TOUCH_DOT_RADIUS), size=(d, d))
            Color(1, 1, 1, 1)
            line = Line(points=[touch.x, touch.y], width=2)
        self._active_lines[touch.uid] = line
        return True

    def on_touch_move(self, touch):
        self._set_status("move", touch)
        line = self._active_lines.get(touch.uid)
        if line:
            line.points += [touch.x, touch.y]
        return True

    def on_touch_up(self, touch):
        self._active_count = max(0, self._active_count - 1)
        self._set_status("up", touch)
        self._active_lines.pop(touch.uid, None)
        # Draw a small dot at lift position
        with self.canvas:
            Color(1.0, 0.4, 0.1, 0.7)
            d = TOUCH_DOT_RADIUS
            Ellipse(pos=(touch.x - d // 2, touch.y - d // 2), size=(d, d))
        if self._active_count == 0:
            Clock.schedule_once(lambda dt: self._set_status("idle"), 0.8)
        return True

    def clear_canvas(self):
        self.canvas.clear()
        self._active_lines.clear()


class TouchTestApp(App):
    def build(self):
        root = FloatLayout()

        status_label = Label(
            text="IDLE",
            font_size=28,
            bold=True,
            color=STATUS_COLORS["idle"],
            size_hint=(1, None),
            height=48,
            pos_hint={"top": 1},
        )

        hint_label = Label(
            text="Touch to draw  |  Double-tap top-right corner to clear",
            font_size=18,
            color=(0.5, 0.5, 0.5, 1),
            size_hint=(1, None),
            height=32,
            pos_hint={"x": 0, "y": 0},
        )

        canvas_widget = DrawCanvas(status_label=status_label, size_hint=(1, 1))
        self._canvas_widget = canvas_widget

        root.add_widget(canvas_widget)
        root.add_widget(status_label)
        root.add_widget(hint_label)

        # Clear on double-tap anywhere in top-right 80x80 px zone
        def check_clear(touch):
            if touch.is_double_tap:
                w, h = root.size
                if touch.x > w - 80 and touch.y > h - 80:
                    canvas_widget.clear_canvas()
                    status_label.text = "IDLE  (cleared)"
                    status_label.color = STATUS_COLORS["idle"]

        root.bind(on_touch_down=lambda *a: check_clear(a[1]))

        return root


if __name__ == "__main__":
    TouchTestApp().run()
