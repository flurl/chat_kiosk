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
    "/home/flurl/development/poga/pothead/plugins/archiver/archives"
    "/bc18157c909a82a4dc858d129895a4ab786c884e01ac27fffe273f32122ccd4f"
    "/messages.jsonl"
)
ATTACHMENTS_DIR    = MESSAGES_FILE.parent / "attachments"
POLL_INTERVAL      = 1.0   # seconds between file-change checks
SLIDESHOW_INTERVAL = 4.0   # seconds per slide during auto-advance


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
C_OVERLAY_BG = (0.00, 0.00, 0.00, 0.94)
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
        source = msg.get('source', '')
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

        if not sent and source:
            inner.add_widget(_lbl(source, size=18, color=C_SUBTEXT))

        if text:
            body = text + (' ✎' if edited else '')
            inner.add_widget(_lbl(body, size=24,
                                  halign='right' if sent else 'left'))

        if images:
            n = len(images)
            inner.add_widget(_lbl(
                f'📷  {n} image{"s" if n > 1 else ""}  —  tap to view',
                size=20, color=C_IMG_LINK,
            ))

        inner.add_widget(_lbl(time_str, size=17, color=C_SUBTEXT, halign='right'))

        # ── rounded background ──────────────────────────────────────────────
        with inner.canvas.before:
            Color(*(C_SENT if sent else C_RECV))
            bubble_bg = RoundedRectangle(radius=[dp(18)])
        inner.bind(
            pos =lambda w, v: setattr(bubble_bg, 'pos',  v),
            size=lambda w, v: setattr(bubble_bg, 'size', v),
        )

        # ── alignment container ─────────────────────────────────────────────
        anchor = AnchorLayout(
            size_hint=(1, None),
            anchor_x='right' if sent else 'left',
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
            '✕', {'right': 0.99, 'top': 0.99},
            lambda: App.get_running_app().close_slideshow(),
            size=(dp(70), dp(70)), fs=30,
        ))
        self.add_widget(_btn('‹', {'x': 0.01, 'center_y': 0.5},
                             lambda: self._go(self._idx - 1)))
        self.add_widget(_btn('›', {'right': 0.99, 'center_y': 0.5},
                             lambda: self._go(self._idx + 1)))

        self._go(0)
        if len(paths) > 1:
            self._timer = Clock.schedule_interval(
                lambda _: self._go(self._idx + 1), SLIDESHOW_INTERVAL)

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

    def add_message(self, msg: dict):
        self._list.add_widget(MessageBubble(msg))

    def scroll_bottom(self):
        Clock.schedule_once(lambda _: setattr(self._scroll, 'scroll_y', 0), 0.15)

    def _send(self, *_):
        text = self._input.text.strip()
        if text:
            # TODO: implement actual message sending
            print(f'[SEND] {text}', flush=True)
            self._input.text = ''


# ═══════════════════════════════════════════════════════════════════════════════
#  Application
# ═══════════════════════════════════════════════════════════════════════════════

class ChatKioskApp(App):
    title = 'Chat Kiosk'

    def build(self):
        if _args.fullscreen:
            Window.fullscreen = 'auto'
        Window.clearcolor = C_BG

        self._root    = FloatLayout()
        self._chat    = ChatScreen(size_hint=(1, 1))
        self._overlay = None
        self._root.add_widget(self._chat)

        msgs = load_messages(MESSAGES_FILE)
        for m in msgs:
            self._chat.add_message(m)
        self._loaded    = len(msgs)
        self._file_size = MESSAGES_FILE.stat().st_size if MESSAGES_FILE.exists() else 0
        self._chat.scroll_bottom()

        Clock.schedule_interval(self._poll, POLL_INTERVAL)
        return self._root

    # ── file polling ────────────────────────────────────────────────────────
    def _poll(self, _dt):
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
