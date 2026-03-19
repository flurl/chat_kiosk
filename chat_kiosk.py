#!/usr/bin/env python3
"""
Chat Kiosk — fullscreen chat display for Raspberry Pi / Raspbian

Reads a Signal-format JSONL archive, polls for new messages,
and shows image attachments in a tap-activated fullscreen slideshow.
"""

import os
import sys
import json
import time
import socket
import subprocess
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
    else:
        Config.set('graphics', 'window_state', 'maximized')
Config.set('input', 'mouse', 'mouse,multitouch_on_demand')
Config.set('kivy', 'keyboard_mode', 'systemanddock')

from kivy.core.text import LabelBase
_DEJAVU = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
_DEJAVU_BOLD = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')
if _DEJAVU.exists():
    LabelBase.register(
        name='Roboto',
        fn_regular=str(_DEJAVU),
        fn_bold=str(_DEJAVU_BOLD) if _DEJAVU_BOLD.exists() else None,
    )

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
from kivy.uix.vkeyboard import VKeyboard

MPV_IPC_SOCKET = '/tmp/chat_kiosk_mpv.sock'


# ═══════════════════════════════════════════════════════════════════════════════
#  Configuration  — defaults; overridden by chat_kiosk.json if present
# ═══════════════════════════════════════════════════════════════════════════════

_CONFIG_FILE = Path(__file__).parent / 'chat_kiosk.json'

def _load_config() -> dict:
    if _CONFIG_FILE.exists():
        try:
            with _CONFIG_FILE.open(encoding='utf-8') as _f:
                return json.load(_f)
        except Exception as _e:
            print(f'[WARN] could not read {_CONFIG_FILE}: {_e}', file=sys.stderr)
    return {}

_cfg = _load_config()

for _required in ('archive_dir', 'outbox_dir'):
    if _required not in _cfg:
        print(f'[ERROR] "{_required}" must be set in {_CONFIG_FILE}', file=sys.stderr)
        sys.exit(1)

ARCHIVE_DIR             = Path(_cfg['archive_dir'])
ATTACHMENTS_DIR         = Path(_cfg['attachments_dir']) if 'attachments_dir' in _cfg else ARCHIVE_DIR / 'attachments'
OUTBOX_DIR              = Path(_cfg['outbox_dir'])
POLL_INTERVAL           = _cfg.get('poll_interval',           1.0)   # seconds between file-change checks
SLIDESHOW_INTERVAL      = _cfg.get('slideshow_interval',      4.0)   # seconds per slide during auto-advance
VIDEO_AUTOPLAY_DELAY    = _cfg.get('video_autoplay_delay',    3)     # seconds countdown before a video slide auto-plays
QUICK_MESSAGES          = _cfg.get('quick_messages',          ['Yes', 'No', 'Perhaps'])
IDLE_NOTIFICATION_DELAY = _cfg.get('idle_notification_delay', 1)     # minutes of inactivity before showing new-message notification
LED_PIN_1               = _cfg.get('led_pin_1',               5)     # BCM GPIO 5  (header pin 29) — notification LED 1
LED_PIN_2               = _cfg.get('led_pin_2',               6)     # BCM GPIO 6  (header pin 31) — notification LED 2
LED_BLINK_INTERVAL      = _cfg.get('led_blink_interval',      10)    # seconds per half-cycle


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

def discover_archive_files(archive_dir: Path) -> list[Path]:
    """Return archive JSONL files sorted newest-first.

    Naming convention:
      TSSTART-TSEND.jsonl  — sealed file
      TSSTART-.jsonl       — current open file (no end timestamp yet)
    """
    if not archive_dir.exists():
        return []
    files = []
    for p in archive_dir.glob('*.jsonl'):
        parts = p.stem.split('-', 1)
        if len(parts) == 2:
            try:
                files.append((int(parts[0]), p))
            except ValueError:
                pass
    files.sort(reverse=True)
    return [p for _, p in files]


def load_archive_file(path: Path, pending_edits: dict, pending_deletes: set) -> list[dict]:
    """Parse one archive file; edits/deletes accumulate in the shared dicts.

    Files are loaded newest-first, so edit/delete records are always encountered
    before the original messages they target (which are in older files).
    Pass 1 collects edit/delete records from this file.
    Pass 2 builds the final message list, applying any pending ops.
    """
    raw: list[dict] = []
    with path.open(encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    raw.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    # Pass 1: collect edits/deletes (including cross-file references to older files)
    for msg in raw:
        mtype = msg.get('type', 'chat')
        tgt   = msg.get('target_sent_timestamp')
        if mtype == 'edit':
            pending_edits[tgt] = msg.get('text')
        elif mtype == 'delete':
            pending_deletes.add(tgt)

    # Pass 2: build chat messages, applying pending ops
    result: list[dict] = []
    for msg in raw:
        if msg.get('type', 'chat') != 'chat':
            continue
        ts = msg.get('timestamp')
        if ts in pending_deletes:
            pending_deletes.discard(ts)
            continue
        if ts in pending_edits:
            msg = {**msg, 'text': pending_edits.pop(ts), 'edited': True}
        result.append(msg)

    return result


def image_attachments(msg: dict) -> list[dict]:
    return [a for a in (msg.get('attachments') or [])
            if a.get('content_type', '').startswith('image/')]


def video_attachments(msg: dict) -> list[dict]:
    return [a for a in (msg.get('attachments') or [])
            if a.get('content_type', '').startswith('video/')]


def attachment_path(ts: int, att: dict) -> Path:
    """Resolve local file path for an attachment.
    Archive naming convention: {timestamp}_{id}_{id}
    If the archiver created a symlink with a guessed extension, prefer that
    so media players can identify the format by filename.
    """
    aid  = att['id']
    base = f"{ts}_{aid}_{aid}"
    for p in ATTACHMENTS_DIR.glob(f"{base}.*"):
        return p
    return ATTACHMENTS_DIR / base


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
        videos = video_attachments(msg)

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

        if videos:
            all_vid_paths = [str(attachment_path(ts, a)) for a in videos]
            vid_row = BoxLayout(
                orientation='horizontal',
                size_hint=(1, None),
                height=dp(60),
                spacing=dp(6),
            )
            for i, att in enumerate(videos):
                idx = i
                btn = Button(
                    text=f'▶  video {i + 1}' if len(videos) > 1 else '▶  play video',
                    font_size=sp(20),
                    size_hint=(1, 1),
                    background_color=(0.10, 0.10, 0.18, 1),
                    background_normal='',
                )
                btn.bind(on_release=lambda _, i=idx: App.get_running_app().open_video(all_vid_paths, i))
                vid_row.add_widget(btn)
            inner.add_widget(vid_row)

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
        self._media_paths = (
            [(str(attachment_path(ts, a)), 'image') for a in images] +
            [(str(attachment_path(ts, a)), 'video') for a in videos]
        )

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos) and self._media_paths:
            App.get_running_app().open_slideshow(self._media_paths)
            return True
        return super().on_touch_down(touch)


# ═══════════════════════════════════════════════════════════════════════════════
#  Slideshow overlay
# ═══════════════════════════════════════════════════════════════════════════════

class SlideshowOverlay(FloatLayout):
    """Full-screen image carousel shown on top of the chat."""

    def __init__(self, galleries: list[list[str]], gallery_idx: int = 0, **kwargs):
        super().__init__(**kwargs)
        self._galleries   = galleries
        self._gallery_idx = gallery_idx
        self._paths       = galleries[gallery_idx] if galleries else []
        self._idx             = 0
        self._at_end          = False
        self._timer           = None
        self._countdown_event = None

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

        # video countdown label — created once, added/removed per video slide
        self._vid_label = Label(
            text='',
            markup=True,
            size_hint=(0.88, 0.82),
            pos_hint={'center_x': 0.5, 'center_y': 0.52},
            halign='center',
            valign='middle',
            color=C_TEXT,
        )
        self._vid_label.bind(size=lambda w, v: setattr(w, 'text_size', v))

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

        legend = Label(
            text='[color=ffffff]●[/color] [Ent] Close     [color=4499ff]●[/color] [←] Previous     [color=ffdd00]●[/color] [→] Next',
            markup=True,
            font_size=sp(20),
            color=C_SUBTEXT,
            size_hint=(1, None),
            height=dp(36),
            pos_hint={'center_x': 0.5, 'y': 0.01},
            halign='center',
            valign='middle',
        )
        self.add_widget(legend)

        self._go(0)
        if len(self._paths) > 1:
            self._timer = Clock.schedule_interval(
                lambda _: self._go(self._idx + 1), SLIDESHOW_INTERVAL)

    def _switch_gallery(self, gallery_idx: int):
        self._gallery_idx = gallery_idx
        self._paths       = self._galleries[gallery_idx]

    def _show_end(self):
        """Show an end-of-galleries message and set the sentinel index."""
        self._at_end          = True
        self._idx             = len(self._paths)   # one past end — used by left-press calc
        self._img.opacity = 0
        if self._vid_label.parent:
            self.remove_widget(self._vid_label)
        self._ctr.text = 'No more images'

    def _manual_go(self, idx: int):
        """Navigate manually and stop the auto-advance timer."""
        if self._timer:
            self._timer.cancel()
            self._timer = None

        if self._at_end:
            # Left press (idx == len-1) exits end state; right press ignored
            if idx < len(self._paths):
                self._at_end = False
                self._go(idx)
            return

        if idx >= len(self._paths):
            next_gi = self._gallery_idx + 1
            if next_gi < len(self._galleries):
                self._switch_gallery(next_gi)
                self._go(0)
            else:
                app = App.get_running_app()
                app._load_older()   # updates self._galleries synchronously via overlay reference
                if self._gallery_idx + 1 < len(self._galleries):
                    self._switch_gallery(self._gallery_idx + 1)
                    self._go(0)
                else:
                    self._show_end()
        elif idx < 0:
            prev_gi = self._gallery_idx - 1
            if prev_gi >= 0:
                self._switch_gallery(prev_gi)
                self._go(len(self._paths) - 1)
            else:
                App.get_running_app().close_slideshow()
        else:
            self._go(idx)

    def _cancel_countdown(self):
        if self._countdown_event:
            self._countdown_event.cancel()
            self._countdown_event = None

    def _start_countdown(self, path: str, n: int):
        self._ctr.text = f'{self._idx + 1} / {len(self._paths)}'

        def _fmt(secs):
            return (
                f'[size={int(sp(26))}]Video in[/size]\n'
                f'[size={int(sp(110))}]{secs}[/size]'
            )

        self._vid_label.text = _fmt(n)
        if self._vid_label.parent is None:
            self.add_widget(self._vid_label)
        remaining = [n]

        def _tick(_dt):
            remaining[0] -= 1
            if remaining[0] <= 0:
                self._cancel_countdown()
                if self._vid_label.parent:
                    self.remove_widget(self._vid_label)
                App.get_running_app().open_video([path])
            else:
                self._vid_label.text = _fmt(remaining[0])

        self._countdown_event = Clock.schedule_interval(_tick, 1.0)

    def _go(self, idx: int):
        self._cancel_countdown()
        self._idx = idx % len(self._paths)
        entry = self._paths[self._idx]
        path, kind = entry if isinstance(entry, tuple) else (entry, 'image')
        if kind == 'video':
            self._img.opacity = 0
            self._start_countdown(path, VIDEO_AUTOPLAY_DELAY)
        else:
            if self._vid_label.parent:
                self.remove_widget(self._vid_label)
            self._img.opacity = 1
            self._img.source  = path if os.path.exists(path) else ''
            self._ctr.text    = f'{self._idx + 1} / {len(self._paths)}'

    def stop(self):
        self._cancel_countdown()
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
            text='[color=4499ff]●[/color] [←] Close     ↺↻ [↑↓] Navigate     [color=ffffff]●[/color] [Ent] Send',
            markup=True,
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
#  New-message notification overlay
# ═══════════════════════════════════════════════════════════════════════════════

class NewMessageOverlay(FloatLayout):
    """Full-screen notification shown when a new message arrives during inactivity."""

    def __init__(self, msg: dict, on_close, **kwargs):
        super().__init__(**kwargs)
        self._on_close = on_close

        with self.canvas.before:
            Color(*C_OVERLAY_BG)
            _bg = Rectangle()
        self.bind(
            pos =lambda w, v: setattr(_bg, 'pos',  v),
            size=lambda w, v: setattr(_bg, 'size', v),
        )

        sender  = msg.get('source_name') or msg.get('source', '') or 'Someone'
        text    = msg.get('text') or ''

        box = BoxLayout(
            orientation='vertical',
            size_hint=(0.70, None),
            pos_hint={'center_x': 0.5, 'center_y': 0.5},
            spacing=dp(20),
            padding=(dp(30), dp(30)),
        )
        box.bind(minimum_height=box.setter('height'))

        def _lbl(txt, size=24, color=C_TEXT, markup=False):
            w = Label(
                text=txt,
                markup=markup,
                font_size=sp(size),
                color=color,
                size_hint=(1, None),
                halign='center',
                valign='top',
                text_size=(Window.width * 0.60, None),
            )
            w.bind(texture_size=lambda inst, val: setattr(inst, 'height', val[1]))
            return w

        box.add_widget(_lbl('New message', size=38))
        if sender:
            box.add_widget(_lbl(f'From: {sender}', size=26, color=C_SUBTEXT))
        if text:
            preview = text[:160] + ('…' if len(text) > 160 else '')
            box.add_widget(_lbl(preview, size=24))
        box.add_widget(_lbl(
            '[color=ffffff]●[/color] [Ent] Dismiss',
            size=20, color=C_SUBTEXT, markup=True,
        ))

        with box.canvas.before:
            Color(*C_RECV)
            box_bg = RoundedRectangle(radius=[dp(18)])
        box.bind(
            pos =lambda w, v: setattr(box_bg, 'pos',  v),
            size=lambda w, v: setattr(box_bg, 'size', v),
        )

        self.add_widget(box)

    def on_touch_down(self, touch):
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
        self._on_scroll_top = None   # installed by app after build
        self._scroll.bind(scroll_y=self._on_scroll_y_change)

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

        # ── legend ──────────────────────────────────────────────────────────
        legend = Label(
            text='[color=ffdd00]●[/color] [→] Gallery     [color=ffffff]●[/color] [Ent] Quick messages\n↺ [↑] Scroll up     ↻ [↓] Scroll down',
            markup=True,
            font_size=sp(18),
            color=C_SUBTEXT,
            size_hint=(1, None),
            height=dp(56),
            halign='center',
            valign='middle',
        )
        self.add_widget(legend)

    def _on_scroll_y_change(self, _instance, value):
        if value >= 1.0 and self._on_scroll_top is not None:
            self._on_scroll_top()

    def prepend_messages(self, msgs, done_cb=None):
        if not msgs:
            if done_cb:
                done_cb()
            return
        sv, grid = self._scroll, self._list
        old_height = grid.height
        # Add in reverse so oldest ends up visually on top
        for msg in reversed(msgs):
            grid.add_widget(MessageBubble(msg), index=len(grid.children))

        attempts = [6]

        def _restore(dt):
            new_height = grid.height
            delta = new_height - old_height
            if delta <= 0:
                # Layout hasn't settled yet — retry
                attempts[0] -= 1
                if attempts[0] > 0:
                    Clock.schedule_once(_restore, 0.15)
                elif done_cb:
                    done_cb()
                return
            sv_h = sv.height
            new_scrollable = new_height - sv_h
            if new_scrollable <= 0:
                new_scroll_y = 1.0
            else:
                old_scrollable = old_height - sv_h
                if old_scrollable > 0:
                    # Old messages didn't move — keep same pixel offset from bottom.
                    new_scroll_y = sv.scroll_y * old_scrollable / new_scrollable
                else:
                    new_scroll_y = 1.0
            sv.scroll_y = new_scroll_y
            # Stop DampedScrollEffect from animating scroll_y back to 1.0
            if sv.effect_y:
                sh = max(0, grid.height - sv.height)
                sv.effect_y.value = -new_scroll_y * sh
                sv.effect_y.velocity = 0
            if done_cb:
                done_cb()
        Clock.schedule_once(_restore, 0.1)

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
        self._video_proc    = None   # running mpv subprocess
        self._video_poll    = None   # Clock handle for exit detection
        self._pending            = {}    # outbox Path → pending MessageBubble
        self._quick_overlay      = None
        self._notification       = None
        self._last_interaction   = time.time()
        self._root.add_widget(self._chat)

        # ── notification LEDs (GPIO 5 + 6, opposite phase) ──────────────────
        try:
            from gpiozero import LED as _GpioLED
            self._led1 = _GpioLED(LED_PIN_1)
            self._led2 = _GpioLED(LED_PIN_2)
        except Exception as _e:
            print(f'[INFO] LED init skipped: {_e}', file=sys.stderr)
            self._led1 = self._led2 = None
        self._led_blink_event = None
        self._led_state       = False   # False → LED1 off, LED2 on; True → LED1 on, LED2 off
        self._stop_led_blink()

        self._pending_edits    = {}
        self._pending_deletes  = set()
        self._loaded_msgs: list[dict] = []
        self._loaded_ts_starts: set[int] = set()
        self._loading_older    = True   # stays True until _fill_screen completes

        all_files = discover_archive_files(ARCHIVE_DIR)
        if all_files:
            first = all_files[0]
            self._loaded_msgs = load_archive_file(
                first, self._pending_edits, self._pending_deletes)
            ts = int(first.stem.split('-')[0])
            self._loaded_ts_starts.add(ts)
            self._current_ts          = ts
            self._current_file_loaded = len(self._loaded_msgs)
            self._current_file_size   = first.stat().st_size
        else:
            self._current_ts          = 0
            self._current_file_loaded = 0
            self._current_file_size   = 0

        for m in self._loaded_msgs:
            self._chat.add_message(m)

        self._galleries = self._collect_galleries(self._loaded_msgs)
        self._chat.scroll_bottom()
        self._chat._on_scroll_top = self._load_older
        self._chat._input.bind(focus=self._on_input_focus)
        Clock.schedule_once(self._fill_screen, 0)

        Clock.schedule_interval(self._poll, POLL_INTERVAL)
        Window.bind(on_key_down=self._on_key_down)
        Window.bind(on_touch_down=self._on_window_touch)
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

        all_files = discover_archive_files(ARCHIVE_DIR)
        if not all_files:
            return
        current_file = next((p for p in all_files if p.stem.endswith('-')), None)
        if current_file is None:
            return
        current_ts = int(current_file.stem.split('-')[0])

        if current_ts != self._current_ts:
            # Rollover: archiver sealed old file and opened a new one
            new_msgs = load_archive_file(
                current_file, self._pending_edits, self._pending_deletes)
            self._loaded_ts_starts.add(current_ts)
            self._current_ts          = current_ts
            self._current_file_loaded = len(new_msgs)
            self._current_file_size   = current_file.stat().st_size
        else:
            size = current_file.stat().st_size
            if size <= self._current_file_size:
                return
            self._current_file_size = size
            fresh    = load_archive_file(current_file, {}, set())
            new_msgs = fresh[self._current_file_loaded:]
            self._current_file_loaded = len(fresh)

        idle_secs = time.time() - self._last_interaction
        idle      = idle_secs >= IDLE_NOTIFICATION_DELAY * 60
        for m in new_msgs:
            self._loaded_msgs.append(m)
            self._chat.add_message(m)
            self._galleries = self._collect_galleries(self._loaded_msgs)
            imgs = image_attachments(m)
            if imgs and not m.get('is_synced', False):
                self.open_slideshow(
                    [(str(attachment_path(m['timestamp'], a)), 'image') for a in imgs])
            vids = video_attachments(m)
            if vids and not m.get('is_synced', False):
                self.open_video([str(attachment_path(m['timestamp'], a)) for a in vids])
            if (idle
                    and not m.get('is_synced', False)
                    and self._overlay is None
                    and self._video_proc is None):
                self.open_notification(m)

        if new_msgs:
            if self._overlay:
                self._overlay._galleries = self._galleries
            self._chat.scroll_bottom()

    def _fill_screen(self, _dt):
        """Load older files until the message list fills the viewport."""
        sv   = self._chat._scroll
        grid = self._chat._list
        # sv.height may still be the Kivy default (100) before the first layout
        # pass; fall back to Window-based estimate in that case.
        viewport_h = max(sv.height, Window.height - dp(86) - dp(30))
        if grid.height >= viewport_h:
            self._loading_older = False
            sv.scroll_y = 0
            return

        # Find next older file
        min_ts = min(self._loaded_ts_starts) if self._loaded_ts_starts else None
        if min_ts is not None:
            all_files = discover_archive_files(ARCHIVE_DIR)
            next_file = next(
                (p for p in all_files
                 if int(p.stem.split('-')[0]) not in self._loaded_ts_starts
                 and int(p.stem.split('-')[0]) < min_ts),
                None,
            )
            if next_file is not None:
                older_msgs = load_archive_file(
                    next_file, self._pending_edits, self._pending_deletes)
                self._loaded_ts_starts.add(int(next_file.stem.split('-')[0]))
                if older_msgs:
                    self._loaded_msgs = older_msgs + self._loaded_msgs
                    self._galleries = self._collect_galleries(self._loaded_msgs)
                    for msg in reversed(older_msgs):
                        grid.add_widget(MessageBubble(msg), index=len(grid.children))
                Clock.schedule_once(self._fill_screen, 0.15)
                return

        # No more files
        self._loading_older = False
        sv.scroll_y = 0

    def _load_older(self):
        if self._loading_older:
            return

        # Find the next older archive file not yet loaded
        min_ts = min(self._loaded_ts_starts) if self._loaded_ts_starts else None
        if min_ts is None:
            return
        all_files = discover_archive_files(ARCHIVE_DIR)
        next_file = next(
            (p for p in all_files
             if int(p.stem.split('-')[0]) not in self._loaded_ts_starts
             and int(p.stem.split('-')[0]) < min_ts),
            None,
        )
        if next_file is None:
            return

        self._loading_older = True
        older_msgs = load_archive_file(next_file, self._pending_edits, self._pending_deletes)
        self._loaded_ts_starts.add(int(next_file.stem.split('-')[0]))

        def _done():
            self._loading_older = False

        if older_msgs:
            self._loaded_msgs = older_msgs + self._loaded_msgs
            # Rebuild galleries synchronously so slideshow can check immediately
            self._galleries = self._collect_galleries(self._loaded_msgs)
            if self._overlay:
                self._overlay._galleries = self._galleries
            self._chat.prepend_messages(older_msgs, done_cb=_done)
        else:
            Clock.schedule_once(lambda _: _done(), 0.1)

    def _on_input_focus(self, _inst, focused):
        if focused:
            Clock.schedule_once(self._find_and_bind_vkb, 0.3)
        else:
            self._resize_for_keyboard(0)

    def _find_and_bind_vkb(self, _dt):
        vkb = next((c for c in Window.children if isinstance(c, VKeyboard)), None)
        if vkb:
            layout_path = str(Path(__file__).parent / 'kiosk_keyboard.json')
            if vkb.layout != layout_path:
                vkb.layout = layout_path
            self._resize_for_keyboard(vkb.height)
            vkb.bind(height=lambda _w, h: self._resize_for_keyboard(h))
        else:
            # Keyboard still animating in — retry
            Clock.schedule_once(self._find_and_bind_vkb, 0.2)

    def _resize_for_keyboard(self, kh):
        self._chat.size_hint_y = None
        self._chat.height = Window.height - kh
        self._chat.y = kh

    def on_stop(self):
        self.close_video()   # terminate mpv if running
        Window.unbind(on_key_down=self._on_key_down)
        Window.unbind(on_touch_down=self._on_window_touch)
        Window.unbind(on_joy_hat=self._on_joy_hat)
        Window.unbind(on_joy_button_down=self._on_joy_button_down)

    def _on_window_touch(self, _win, _touch):
        self._last_interaction = time.time()

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
        self._last_interaction = time.time()
        if self._notification is not None:
            if key in (13, 27, 276):                        # enter, escape, left
                self.close_notification()
                return True
        if self._overlay is not None:
            if key == 276:                              # left arrow
                self._overlay._manual_go(self._overlay._idx - 1)
                return True
            if key == 275:                              # right arrow
                self._overlay._manual_go(self._overlay._idx + 1)
                return True
            if key in (13, 27):                         # enter or escape
                self.close_slideshow()
                return True
        if self._video_proc is not None:
            if key == 13:                              # enter — play/pause
                self._mpv_command('cycle', 'pause')
                return True
            if key in (27, 276):                       # escape or left — close
                self.close_video()
                return True
            return True                                # swallow all other keys
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
            if key in (27, 276):                        # escape or left arrow
                self.close_quick_messages()
                return True
        elif key == 273:                                    # up — scroll toward older messages
            self._scroll_chat(+1)
            return True
        elif key == 274:                                    # down — scroll toward newer messages
            self._scroll_chat(-1)
            return True
        elif key == 13 and not self._chat._input.focus:   # enter
            self.open_quick_messages()
            return True
        elif key == 275 and self._galleries:            # right arrow — open first gallery
            self.open_slideshow(self._galleries[0])
            return True
        return False

    def _scroll_chat(self, direction: int):
        sv   = self._chat._scroll
        grid = self._chat._list
        scrollable = max(1, grid.height - sv.height)
        step = dp(150) / scrollable
        sv.scroll_y = max(0.0, min(1.0, sv.scroll_y + direction * step))

    # ── video (mpv subprocess) ────────────────────────────────────────────────
    def open_video(self, paths: list[str], idx: int = 0):
        self.close_video()
        path = paths[idx]
        if not os.path.exists(path):
            return
        try:
            self._video_proc = subprocess.Popen([
                'mpv', '--fullscreen', '--really-quiet',
                f'--input-ipc-server={MPV_IPC_SOCKET}',
                path,
            ])
        except FileNotFoundError:
            print('[ERROR] mpv not found — install it with: sudo apt install mpv',
                  file=sys.stderr)
            return
        self._video_poll = Clock.schedule_interval(self._check_video_proc, 0.5)

    def _mpv_command(self, *args):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect(MPV_IPC_SOCKET)
                s.sendall(json.dumps({'command': list(args)}).encode() + b'\n')
        except Exception:
            pass

    def _check_video_proc(self, _dt):
        if self._video_proc and self._video_proc.poll() is not None:
            self._video_proc = None
            if self._video_poll:
                self._video_poll.cancel()
                self._video_poll = None
            # Natural exit — advance to next slide if slideshow is open
            if self._overlay:
                self._overlay._manual_go(self._overlay._idx + 1)

    def close_video(self):
        if self._video_poll:
            self._video_poll.cancel()
            self._video_poll = None
        if self._video_proc:
            self._mpv_command('quit')
            self._video_proc = None

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

    # ── notification LEDs ─────────────────────────────────────────────────────
    def _start_led_blink(self):
        if self._led1 is None:
            return
        self._led_state = False
        self._led_blink_event = Clock.schedule_interval(
            self._led_blink_tick, LED_BLINK_INTERVAL)
        self._led_blink_tick(0)   # apply initial state immediately

    def _led_blink_tick(self, _dt):
        self._led_state = not self._led_state
        if self._led_state:
            self._led1.off()
            self._led2.on()
        else:
            self._led1.on()
            self._led2.off()

    def _stop_led_blink(self):
        if self._led_blink_event:
            self._led_blink_event.cancel()
            self._led_blink_event = None
        if self._led1:
            self._led1.on()
            self._led2.on()

    # ── new-message notification ──────────────────────────────────────────────
    def open_notification(self, msg: dict):
        self.close_notification()
        self._notification = NewMessageOverlay(
            msg, on_close=self.close_notification, size_hint=(1, 1))
        self._root.add_widget(self._notification)
        self._start_led_blink()

    def close_notification(self):
        if self._notification:
            self._root.remove_widget(self._notification)
            self._notification = None
        self._stop_led_blink()

    # ── gallery helpers ───────────────────────────────────────────────────────
    @staticmethod
    def _collect_galleries(msgs: list[dict]) -> list[list[tuple]]:
        result = []
        for m in msgs:
            imgs = image_attachments(m)
            vids = video_attachments(m)
            items = (
                [(str(attachment_path(m['timestamp'], a)), 'image') for a in imgs] +
                [(str(attachment_path(m['timestamp'], a)), 'video') for a in vids]
            )
            if items:
                result.append(items)
        return list(reversed(result))

    # ── slideshow control ────────────────────────────────────────────────────
    def open_slideshow(self, paths: list[str]):
        self.close_slideshow()
        gallery_idx = next(
            (i for i, g in enumerate(self._galleries) if g == paths), 0)
        self._overlay = SlideshowOverlay(self._galleries, gallery_idx, size_hint=(1, 1))
        self._root.add_widget(self._overlay)

    def close_slideshow(self):
        if self._overlay:
            self._overlay.stop()
            self._root.remove_widget(self._overlay)
            self._overlay = None


if __name__ == '__main__':
    ChatKioskApp().run()
