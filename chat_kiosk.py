#!/usr/bin/env python3
"""
Chat Kiosk — fullscreen chat display for Raspberry Pi / Raspbian

Reads a Signal-format JSONL archive, polls for new messages,
and shows image attachments in a tap-activated fullscreen slideshow.
"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

# ── CLI args — must be parsed before Kivy touches sys.argv ────────────────────
def _parse_args():
    p = argparse.ArgumentParser(
        description='Chat Kiosk — fullscreen Signal chat viewer',
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument(
        '--fullscreen', action='store_true',
        help='run in fullscreen mode',
    )
    p.add_argument(
        '--size', metavar='WxH', default=None,
        help='window size when not fullscreen, e.g. --size 1024x600',
    )
    args, remaining = p.parse_known_args()
    sys.argv = [sys.argv[0]] + remaining   # strip our flags before Kivy sees them
    return args

_args = _parse_args()

import kivy
kivy.require('2.0.0')

from kivy.config import Config
if _args.fullscreen:
    Config.set('graphics', 'fullscreen', 'auto')
else:
    Config.set('graphics', 'fullscreen', '0')
    if _args.size:
        try:
            w, h = _args.size.lower().split('x')
            Config.set('graphics', 'width',  w.strip())
            Config.set('graphics', 'height', h.strip())
        except ValueError:
            print(f'[WARN] invalid --size value "{_args.size}", expected WxH e.g. 1024x600',
                  file=sys.stderr)
Config.set('input', 'mouse', 'mouse,multitouch_on_demand')

from kivy.app import App
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.image import Image
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle, RoundedRectangle
from kivy.metrics import dp, sp


# ═══════════════════════════════════════════════════════════════════════════════
#  Configuration  — edit these paths for your deployment
# ═══════════════════════════════════════════════════════════════════════════════

MESSAGES_FILE = Path(
    "/home/senior/senior-connect-box/pothead/plugins/archiver/archives"
    "/bc18157c909a82a4dc858d129895a4ab786c884e01ac27fffe273f32122ccd4f"
    "/messages.jsonl"
)
ATTACHMENTS_DIR    = MESSAGES_FILE.parent / "attachments"
OUTBOX_DIR         = Path(
    "/home/senior/senior-connect-box/pothead/plugins/filesender/outbox"
    "/bc18157c909a82a4dc858d129895a4ab786c884e01ac27fffe273f32122ccd4f"
)
POLL_INTERVAL      = 1.0   # seconds between file-change checks
SLIDESHOW_INTERVAL = 4.0   # seconds per slide during auto-advance
QUICK_MESSAGES     = ['Yes', 'No', 'Perhaps']


# ═══════════════════════════════════════════════════════════════════════════════
#  Colour palette  (dark theme)
# ═══════════════════════════════════════════════════════════════════════════════

C_BG         = (0.10, 0.10, 0.13, 1)
C_SENT       = (0.12, 0.42, 0.36, 1)   # outgoing bubble  — warm teal
C_RECV       = (0.26, 0.26, 0.31, 1)   # incoming bubble  — medium warm grey
C_TEXT       = (1.00, 1.00, 1.00, 1)   # pure white for maximum contrast
C_SUBTEXT    = (0.86, 0.86, 0.88, 1)   # near-white — readable on any bubble
C_INPUT_BG   = (0.16, 0.16, 0.20, 1)
C_SEND_BTN   = (0.12, 0.52, 0.38, 1)   # matches sent-bubble teal
C_PENDING    = (0.55, 0.30, 0.05, 1)   # pending bubble  — burnt orange
C_OVERLAY_BG = (0.00, 0.00, 0.00, 0.94)
C_QUICK_HL   = (0.16, 0.52, 0.72, 1)   # quick-message selected  — steel blue
C_IMG_LINK   = (0.65, 0.92, 1.00, 1)   # bright sky-blue, visible on both bubbles

BUBBLE_WIDTH_FRAC = 0.74   # max fraction of screen width per bubble


# ═══════════════════════════════════════════════════════════════════════════════
#  Message model
# ═══════════════════════════════════════════════════════════════════════════════

def load_messages(path: Path) -> list[dict]:
    """Parse JSONL file, apply edits/deletes, return final message list."""
    if not path.exists():
        return []
    raw: list[dict] = []
    with path.open(encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    raw.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    by_ts: dict[int, dict] = {}
    order: list[int] = []
    for msg in raw:
        mtype = msg.get('type', 'chat')
        ts    = msg.get('timestamp')
        tgt   = msg.get('target_sent_timestamp')
        if mtype == 'chat':
            by_ts[ts] = msg
            order.append(ts)
        elif mtype == 'edit' and tgt in by_ts:
            by_ts[tgt] = {**by_ts[tgt], 'text': msg.get('text'), 'edited': True}
        elif mtype == 'delete' and tgt in by_ts:
            del by_ts[tgt]

    return [by_ts[ts] for ts in order if ts in by_ts]


def image_attachments(msg: dict) -> list[dict]:
    return [a for a in (msg.get('attachments') or [])
            if a.get('content_type', '').startswith('image/')]


def attachment_path(ts: int, att: dict) -> Path:
    """Resolve local file path for an attachment.
    Archive naming convention: {timestamp}_{id}_{id}
    """
    aid = att['id']
    return ATTACHMENTS_DIR / f"{ts}_{aid}_{aid}"


# ═══════════════════════════════════════════════════════════════════════════════
#  Chat bubble
# ═══════════════════════════════════════════════════════════════════════════════

class MessageBubble(BoxLayout):
    """One chat message — aligned right (sent) or left (received)."""

    def __init__(self, msg: dict, **kwargs):
        super().__init__(
            orientation='vertical',
            size_hint=(1, None),
            height=dp(60),      # placeholder; updated by content height chain
            **kwargs,
        )
        sent   = msg.get('is_synced', False)   # synced = sent from our account
        text   = msg.get('text') or ''
        ts     = msg.get('timestamp', 0)
        source = msg.get('source_name') or msg.get('source', '')
        edited = msg.get('edited', False)
        images = image_attachments(msg)

        try:
            time_str = datetime.fromtimestamp(ts / 1000).strftime('%H:%M')
        except Exception:
            time_str = ''

        bub_w   = Window.width * BUBBLE_WIDTH_FRAC
        inner_w = bub_w - dp(40)

        # ── inner GridLayout (its minimum_height drives the bubble height) ──
        inner = GridLayout(
            cols=1,
            size_hint=(None, None),
            width=bub_w,
            spacing=dp(6),
            padding=(dp(20), dp(14)),
        )
        inner.bind(minimum_height=inner.setter('height'))

        def _lbl(txt, size=24, color=C_TEXT, halign='left'):
            w = Label(
                text=txt,
                font_size=sp(size),
                color=color,
                size_hint=(1, None),
                halign=halign,
                valign='top',
                text_size=(inner_w, None),
            )
            w.bind(texture_size=lambda inst, val: setattr(inst, 'height', val[1]))
            return w

        display_name = source or ('Me' if sent else '')
        if display_name:
            inner.add_widget(_lbl(display_name, size=18, color=C_SUBTEXT))

        if text:
            body = text + (' (edited)' if edited else '')
            inner.add_widget(_lbl(body, size=24,
                                  halign='right' if sent else 'left'))

        if images:
            MAX_THUMBS = 3
            thumb_h = dp(80)
            shown = images[:MAX_THUMBS]
            extra = len(images) - len(shown)

            row = BoxLayout(
                orientation='horizontal',
                size_hint=(1, None),
                height=thumb_h,
                spacing=dp(6),
            )
            for att in shown:
                p = str(attachment_path(ts, att))
                row.add_widget(Image(
                    source=p if os.path.exists(p) else '',
                    size_hint=(None, 1),
                    width=thumb_h,
                    allow_stretch=True,
                    keep_ratio=True,
                ))
            if extra > 0:
                row.add_widget(Label(
                    text=f'+{extra} more',
                    font_size=sp(18),
                    color=C_IMG_LINK,
                    size_hint=(1, 1),
                    halign='left',
                    valign='middle',
                ))
            inner.add_widget(row)

        if msg.get('pending'):
            inner.add_widget(_lbl('Sending...', size=17, color=C_IMG_LINK, halign='right'))
        else:
            inner.add_widget(_lbl(time_str, size=17, color=C_SUBTEXT, halign='right'))

        # ── rounded background ──────────────────────────────────────────────
        pending = msg.get('pending', False)
        with inner.canvas.before:
            Color(*(C_PENDING if pending else (C_SENT if sent else C_RECV)))
            bubble_bg = RoundedRectangle(radius=[dp(18)])
        inner.bind(
            pos =lambda w, v: setattr(bubble_bg, 'pos',  v),
            size=lambda w, v: setattr(bubble_bg, 'size', v),
        )

        # ── alignment container ─────────────────────────────────────────────
        anchor = AnchorLayout(
            size_hint=(1, None),
            anchor_x='center' if pending else ('right' if sent else 'left'),
            anchor_y='top',
        )
        anchor.add_widget(inner)

        # height chain: inner.height → anchor.height → self.height
        inner.bind( height=lambda w, v: setattr(anchor, 'height', v + dp(8)))
        anchor.bind(height=lambda w, v: setattr(self,   'height', v))

        self.add_widget(anchor)

        # ── tap to open slideshow ───────────────────────────────────────────
        self._img_paths = [str(attachment_path(ts, a)) for a in images]

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos) and self._img_paths:
            App.get_running_app().open_slideshow(self._img_paths)
            return True
        return super().on_touch_down(touch)


# ═══════════════════════════════════════════════════════════════════════════════
#  Slideshow overlay
# ═══════════════════════════════════════════════════════════════════════════════

class SlideshowOverlay(FloatLayout):
    """Full-screen image carousel shown on top of the chat."""

    def __init__(self, paths: list[str], **kwargs):
        super().__init__(**kwargs)
        self._paths = paths
        self._idx   = 0
        self._timer = None

        # dim background
        with self.canvas.before:
            Color(*C_OVERLAY_BG)
            _bg = Rectangle()
        self.bind(
            pos =lambda w, v: setattr(_bg, 'pos',  v),
            size=lambda w, v: setattr(_bg, 'size', v),
        )

        # image widget
        self._img = Image(
            size_hint=(0.88, 0.82),
            pos_hint={'center_x': 0.5, 'center_y': 0.52},
            allow_stretch=True,
            keep_ratio=True,
        )
        self.add_widget(self._img)

        # slide counter  "2 / 4"
        self._ctr = Label(
            size_hint=(1, None), height=dp(44),
            pos_hint={'center_x': 0.5, 'top': 0.97},
            font_size=sp(22), color=C_TEXT,
        )
        self.add_widget(self._ctr)

        def _btn(text, pos_hint, callback, size=(dp(80), dp(110)), fs=48):
            b = Button(
                text=text, font_size=sp(fs),
                size_hint=(None, None), size=size,
                pos_hint=pos_hint,
                background_color=(0.20, 0.20, 0.25, 0.85),
                background_normal='',
            )
            b.bind(on_release=lambda _: callback())
            return b

        self.add_widget(_btn(
            'X', {'right': 0.99, 'top': 0.99},
            lambda: App.get_running_app().close_slideshow(),
            size=(dp(70), dp(70)), fs=30,
        ))
        self.add_widget(_btn('‹', {'x': 0.01, 'center_y': 0.5},
                             lambda: self._manual_go(self._idx - 1)))
        self.add_widget(_btn('›', {'right': 0.99, 'center_y': 0.5},
                             lambda: self._manual_go(self._idx + 1)))

        self._go(0)
        if len(paths) > 1:
            self._timer = Clock.schedule_interval(
                lambda _: self._go(self._idx + 1), SLIDESHOW_INTERVAL)

    def _manual_go(self, idx: int):
        """Navigate manually and stop the auto-advance timer."""
        self._go(idx)
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _go(self, idx: int):
        self._idx = idx % len(self._paths)
        p = self._paths[self._idx]
        self._img.source = p if os.path.exists(p) else ''
        self._ctr.text   = f'{self._idx + 1} / {len(self._paths)}'

    def stop(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None


# ═══════════════════════════════════════════════════════════════════════════════
#  Quick-message overlay  (M key)
# ═══════════════════════════════════════════════════════════════════════════════

class QuickMessageOverlay(FloatLayout):
    """Full-screen overlay for picking and sending a predefined message."""

    def __init__(self, messages: list[str], on_send, on_close, **kwargs):
        super().__init__(**kwargs)
        self._messages = messages
        self._on_send  = on_send
        self._on_close = on_close
        self._sel      = 0
        self._btns: list[Button] = []

        with self.canvas.before:
            Color(*C_OVERLAY_BG)
            _bg = Rectangle()
        self.bind(
            pos =lambda w, v: setattr(_bg, 'pos',  v),
            size=lambda w, v: setattr(_bg, 'size', v),
        )

        n       = len(messages)
        btn_h   = dp(72)
        spc     = dp(14)
        title_h = dp(60)
        hint_h  = dp(38)
        pad_v   = dp(28)
        # BoxLayout spacing fires between each of the (n+2) children → (n+1) gaps
        box_h   = title_h + n * btn_h + hint_h + (n + 1) * spc + 2 * pad_v

        box = BoxLayout(
            orientation='vertical',
            size_hint=(0.55, None),
            height=box_h,
            pos_hint={'center_x': 0.5, 'center_y': 0.5},
            spacing=spc,
            padding=(dp(24), pad_v),
        )

        title = Label(
            text='Quick Messages',
            font_size=sp(28),
            color=C_TEXT,
            size_hint=(1, None),
            height=title_h,
        )
        box.add_widget(title)

        for i, msg in enumerate(messages):
            btn = Button(
                text=msg,
                font_size=sp(26),
                size_hint=(1, None),
                height=btn_h,
                background_normal='',
                background_color=C_QUICK_HL if i == 0 else C_RECV,
            )
            btn.bind(on_release=lambda _b, m=msg: self._on_send(m))
            self._btns.append(btn)
            box.add_widget(btn)

        hint = Label(
            text='[UP] / [DOWN]  navigate     [ENTER]  send     [ESC]  close',
            font_size=sp(16),
            color=C_SUBTEXT,
            size_hint=(1, None),
            height=hint_h,
        )
        box.add_widget(hint)

        self.add_widget(box)

    def move(self, delta: int):
        self._btns[self._sel].background_color = C_RECV
        self._sel = (self._sel + delta) % len(self._messages)
        self._btns[self._sel].background_color = C_QUICK_HL

    def selected_text(self) -> str:
        return self._messages[self._sel]

    def on_touch_down(self, touch):
        if not super().on_touch_down(touch):
            self._on_close()
        return True


# ═══════════════════════════════════════════════════════════════════════════════
#  Main chat screen
# ═══════════════════════════════════════════════════════════════════════════════

class ChatScreen(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation='vertical', **kwargs)

        with self.canvas.before:
            Color(*C_BG)
            _bg = Rectangle()
        self.bind(
            pos =lambda w, v: setattr(_bg, 'pos',  v),
            size=lambda w, v: setattr(_bg, 'size', v),
        )

        # ── scrollable message list ─────────────────────────────────────────
        scroll = ScrollView(
            size_hint=(1, 1),
            do_scroll_x=False,
            bar_width=dp(3),
            scroll_type=['bars', 'content'],
        )
        self._list = GridLayout(
            cols=1,
            size_hint=(1, None),
            spacing=dp(8),
            padding=(dp(12), dp(12)),
        )
        self._list.bind(minimum_height=self._list.setter('height'))
        scroll.add_widget(self._list)
        self.add_widget(scroll)
        self._scroll = scroll

        # ── input bar ───────────────────────────────────────────────────────
        bar = BoxLayout(
            size_hint=(1, None), height=dp(86),
            padding=(dp(12), dp(12)), spacing=dp(10),
        )
        with bar.canvas.before:
            Color(*C_INPUT_BG)
            bar_bg = Rectangle()
        bar.bind(
            pos =lambda w, v: setattr(bar_bg, 'pos',  v),
            size=lambda w, v: setattr(bar_bg, 'size', v),
        )

        self._input = TextInput(
            hint_text='Type a message…',
            multiline=False,
            size_hint=(1, 1),
            font_size=sp(22),
            foreground_color=C_TEXT,
            background_color=(*C_BG[:3], 1),
            cursor_color=C_TEXT,
            padding=(dp(16), dp(14)),
        )
        self._input.bind(on_text_validate=self._send)

        send_btn = Button(
            text='Send',
            size_hint=(None, 1), width=dp(120),
            font_size=sp(22),
            background_color=C_SEND_BTN,
            background_normal='',
        )
        send_btn.bind(on_release=self._send)

        bar.add_widget(self._input)
        bar.add_widget(send_btn)
        self.add_widget(bar)

    def add_message(self, msg: dict) -> 'MessageBubble':
        bubble = MessageBubble(msg)
        self._list.add_widget(bubble)
        return bubble

    def scroll_bottom(self):
        Clock.schedule_once(lambda _: setattr(self._scroll, 'scroll_y', 0), 0.15)

    def _send(self, *_):
        text = self._input.text.strip()
        if text:
            self._input.text = ''
            App.get_running_app().send_message(text)


# ═══════════════════════════════════════════════════════════════════════════════
#  Application
# ═══════════════════════════════════════════════════════════════════════════════

class ChatKioskApp(App):
    title = 'Chat Kiosk'

    def build(self):
        if _args.fullscreen:
            Window.fullscreen = 'auto'
        Window.clearcolor = C_BG

        self._root          = FloatLayout()
        self._chat          = ChatScreen(size_hint=(1, 1))
        self._overlay       = None
        self._pending       = {}    # outbox Path → pending MessageBubble
        self._quick_overlay = None
        self._root.add_widget(self._chat)

        msgs = load_messages(MESSAGES_FILE)
        for m in msgs:
            self._chat.add_message(m)
        self._loaded    = len(msgs)
        self._file_size = MESSAGES_FILE.stat().st_size if MESSAGES_FILE.exists() else 0
        self._chat.scroll_bottom()

        Clock.schedule_interval(self._poll, POLL_INTERVAL)
        Window.bind(on_key_down=self._on_key_down)
        Window.bind(on_joy_hat=self._on_joy_hat)
        Window.bind(on_joy_button_down=self._on_joy_button_down)
        return self._root

    # ── outbox / send ────────────────────────────────────────────────────────
    def send_message(self, text: str):
        ts = int(datetime.now().timestamp() * 1000)
        outfile = OUTBOX_DIR / f'{ts}.md'
        try:
            OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
            outfile.write_text(text, encoding='utf-8')
        except OSError as e:
            print(f'[ERROR] could not write outbox file: {e}', file=sys.stderr)
            return
        msg = {
            'type': 'chat',
            'timestamp': ts,
            'text': text,
            'is_synced': True,
            'pending': True,
        }
        bubble = self._chat.add_message(msg)
        self._chat.scroll_bottom()
        self._pending[outfile] = bubble

    # ── file polling ────────────────────────────────────────────────────────
    def _poll(self, _dt):
        # Remove pending bubbles whose outbox files have been deleted
        for outfile in list(self._pending.keys()):
            if not outfile.exists():
                bubble = self._pending.pop(outfile)
                self._chat._list.remove_widget(bubble)

        if not MESSAGES_FILE.exists():
            return
        size = MESSAGES_FILE.stat().st_size
        if size <= self._file_size:
            return
        self._file_size = size

        msgs = load_messages(MESSAGES_FILE)
        new  = msgs[self._loaded:]
        for m in new:
            self._chat.add_message(m)
            imgs = image_attachments(m)
            # auto-open slideshow for incoming messages that have images
            if imgs and not m.get('is_synced', False):
                self.open_slideshow(
                    [str(attachment_path(m['timestamp'], a)) for a in imgs])
        self._loaded += len(new)
        if new:
            self._chat.scroll_bottom()

    def on_stop(self):
        Window.unbind(on_key_down=self._on_key_down)
        Window.unbind(on_joy_hat=self._on_joy_hat)
        Window.unbind(on_joy_button_down=self._on_joy_button_down)

    # ── joystick ─────────────────────────────────────────────────────────────
    def _on_joy_hat(self, _win, _stick, _hat, value):
        x, y = value
        if y == -1:
            self._on_key_down(None, 273, 0, None, [])   # up arrow
        elif y == 1:
            self._on_key_down(None, 274, 0, None, [])   # down arrow

    def _on_joy_button_down(self, _win, _stick, button):
        if button == 0:
            self._on_key_down(None, 13, 0, None, [])    # enter
        elif button == 1:
            self._on_key_down(None, 276, 0, None, [])   # left arrow
        elif button == 2:
            self._on_key_down(None, 275, 0, None, [])   # right arrow

    # ── keyboard ─────────────────────────────────────────────────────────────
    def _on_key_down(self, _win, key, _sc, _cp, _mod):
        if self._overlay is not None:
            if key == 276:                              # left arrow
                self._overlay._manual_go(self._overlay._idx - 1)
                return True
            if key == 275:                              # right arrow
                self._overlay._manual_go(self._overlay._idx + 1)
                return True
            if key == 27:                               # escape
                self.close_slideshow()
                return True
        if self._quick_overlay is not None:
            if key == 273:                              # up
                self._quick_overlay.move(-1)
                return True
            if key == 274:                              # down
                self._quick_overlay.move(1)
                return True
            if key == 13:                               # enter
                text = self._quick_overlay.selected_text()
                self.close_quick_messages()
                self.send_message(text)
                return True
            if key == 27:                               # escape
                self.close_quick_messages()
                return True
        elif key == ord('m') and not self._chat._input.focus:
            self.open_quick_messages()
            return True
        return False

    # ── quick-message overlay ─────────────────────────────────────────────────
    def open_quick_messages(self):
        self._quick_overlay = QuickMessageOverlay(
            QUICK_MESSAGES,
            on_send=self._quick_send,
            on_close=self.close_quick_messages,
            size_hint=(1, 1),
        )
        self._root.add_widget(self._quick_overlay)

    def close_quick_messages(self):
        if self._quick_overlay:
            self._root.remove_widget(self._quick_overlay)
            self._quick_overlay = None

    def _quick_send(self, text: str):
        self.close_quick_messages()
        self.send_message(text)

    # ── slideshow control ────────────────────────────────────────────────────
    def open_slideshow(self, paths: list[str]):
        self.close_slideshow()
        self._overlay = SlideshowOverlay(paths, size_hint=(1, 1))
        self._root.add_widget(self._overlay)

    def close_slideshow(self):
        if self._overlay:
            self._overlay.stop()
            self._root.remove_widget(self._overlay)
            self._overlay = None


if __name__ == '__main__':
    ChatKioskApp().run()
