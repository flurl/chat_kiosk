# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running

```bash
.venv/bin/python chat_kiosk.py [--fullscreen] [--size WxH]
```

There are no tests and no linter configured. The entire application is a single file: `chat_kiosk.py`.

## Architecture

The app is a single-file Kivy application with one-way data flow:

```
messages.jsonl (polled every 1 s)
    → load_messages()       # parse JSONL, apply edits/deletes → list[dict]
    → ChatScreen            # scrollable list of MessageBubble widgets
        → SlideshowOverlay  # fullscreen image carousel (tap or auto-open)
        → QuickMessageOverlay  # predefined-reply picker (M key / button 0)
    → OUTBOX_DIR/<ts>.md    # written on send; deleted by external process
```

**Input routing** — all joystick events delegate to `_on_key_down` so keyboard and hardware inputs share a single handler. Priority order in `_on_key_down`: slideshow overlay → quick-message overlay → normal mode.

**Kivy key codes** relevant to this project: up=273, down=274, right=275, left=276, enter=13, escape=27.

**Pending messages** — when the user sends a message, a temporary bubble (`pending=True`) is added immediately and tracked in `self._pending` (outbox Path → bubble). The poll loop removes the bubble and path once the outbox file is deleted by the external delivery process.

**Height chain** — Kivy requires explicit heights for widgets inside `GridLayout(size_hint_y=None)`. The pattern used here is: `inner.minimum_height → inner.height → anchor.height → bubble.height`, driven by `bind` callbacks.

## Configuration constants (top of `chat_kiosk.py`)

| Constant | Purpose |
|---|---|
| `MESSAGES_FILE` | Path to the JSONL archive |
| `ATTACHMENTS_DIR` | Directory of attachment files (named `<ts>_<id>_<id>`) |
| `OUTBOX_DIR` | Where outgoing `.md` files are written |
| `POLL_INTERVAL` | File-change check frequency (seconds) |
| `SLIDESHOW_INTERVAL` | Auto-advance delay (seconds) |
| `QUICK_MESSAGES` | List of predefined replies |

## Hardware controls

Physical controls and their colors:
- ⚪ Button 0 — white button
- 🔵 Button 1 — blue button
- 🟡 Button 2 — yellow button
- ↺ Hat up (y=-1) — rotary encoder CCW
- ↻ Hat down (y=1) — rotary encoder CW

All map to keyboard keys via `_on_key_down`. When writing shortcut legends (both in UI labels and in this file), **always include both the colored circle and the corresponding keyboard key**:

```
⚪ [Enter]   action
🔵 [←]       action
🟡 [→]       action
↺  [↑]       action
↻  [↓]       action
```

In Kivy labels use `markup=True` with `[color=hex]●[/color]` instead of Unicode circle emoji, and include the key in brackets, e.g.: `[color=ffffff]●[/color] [Ent] Close`

Current shortcut legend by mode:

**Normal mode**
| Input | Key | Action |
|---|---|---|
| ⚪ | Enter | Open quick-message overlay (only when text input is not focused) |
| 🔵 | ← | (no-op) |
| 🟡 | → | Open gallery (newest message with images) |
| ↺ | ↑ | (no-op) |
| ↻ | ↓ | (no-op) |

**Slideshow overlay**
| Input | Key | Action |
|---|---|---|
| ⚪ | Enter | Close slideshow |
| 🔵 | ← | Previous image |
| 🟡 | → | Next image |

**Quick-message overlay**
| Input | Key | Action |
|---|---|---|
| ⚪ | Enter | Send selected message |
| 🔵 | ← | Close without sending |
| ↺ | ↑ | Select previous message |
| ↻ | ↓ | Select next message |
