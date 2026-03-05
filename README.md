# Chat Kiosk

A fullscreen touchscreen chat UI for Raspberry Pi / Raspbian, built with [Kivy](https://kivy.org).

Reads messages from a Signal-format JSONL archive, watches for new ones in real time, and displays image attachments as a fullscreen slideshow.

## Features

- Dark-themed, fullscreen kiosk mode
- Chat bubbles — outgoing (right/teal), incoming (left/grey), pending (centre/orange)
- Handles message edits and deletes from the archive
- Tap any message with images to open a fullscreen slideshow
- Incoming messages with images automatically trigger the slideshow
- Scrollable message history, auto-scrolls to latest message
- Text input bar with Send button — writes message to an outbox file for delivery
- Sent messages shown immediately as a "Sending…" bubble; replaced by the archived copy once delivered
- Quick-message overlay (press `M`) — pick a predefined reply with arrow keys and Enter
- Polls the JSONL archive every second for new messages

## Requirements

### Python

- Python 3.11+
- Kivy 2.3+

### System packages (Raspbian / Debian)

Install these before creating the venv on the Pi:

```bash
sudo apt install python3-dev libsdl2-dev libsdl2-image-dev \
  libsdl2-mixer-dev libsdl2-ttf-dev libportmidi-dev \
  libswscale-dev libavformat-dev libavcodec-dev zlib1g-dev
```

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Configuration

Edit the constants at the top of `chat_kiosk.py`:

| Constant            | Default                        | Description                                        |
|---------------------|--------------------------------|----------------------------------------------------|
| `MESSAGES_FILE`     | path to `messages.jsonl`       | Full path to the JSONL archive file                |
| `ATTACHMENTS_DIR`   | `<archive dir>/attachments/`   | Directory containing attachment files              |
| `OUTBOX_DIR`        | path to outbox directory       | Directory where outgoing message files are written |
| `POLL_INTERVAL`     | `1.0`                          | Seconds between file-change checks                 |
| `SLIDESHOW_INTERVAL`| `4.0`                          | Seconds per slide during auto-advance              |
| `QUICK_MESSAGES`    | `['Yes', 'No', 'Perhaps']`     | Predefined replies shown in the quick-message overlay |

## Running

```bash
.venv/bin/python chat_kiosk.py [--fullscreen] [--size WxH]
```

| Flag | Description |
|------|-------------|
| `--fullscreen` | Run in fullscreen mode |
| `--size WxH` | Set window size when not fullscreen, e.g. `--size 1024x600` |

To exit fullscreen during development, press `F11` or `Escape`.

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| `M` | Open quick-message overlay (only when text input is not focused) |
| `↑` / `↓` | Navigate items in the quick-message overlay (wraps around) |
| `Enter` | Send the selected quick message |
| `Esc` | Close the quick-message overlay without sending |

## Archive format

Messages are read from a JSONL file — one JSON object per line:

```json
{"source": "+43123456789", "type": "chat", "timestamp": 1234567890000,
 "is_synced": true, "text": "Hello!", "attachments": [], "quote": null}
```

Supported message types: `chat`, `edit`, `delete`.

Attachment files are expected at:
```
<attachments_dir>/<timestamp>_<id>_<id>
```

`is_synced: true` means the message was sent from our account (displayed on the right). `is_synced: false` means it was received (displayed on the left).

## Sending messages

When the user sends a message (via the input bar or the quick-message overlay), the kiosk writes it as a Markdown file into `OUTBOX_DIR`:

```
<outbox_dir>/<timestamp_ms>.md
```

An external process is expected to pick up the file, deliver it, and delete it. While the file exists the message is shown as a centred orange "Sending…" bubble. Once the file is deleted the kiosk removes the pending bubble; the delivered message will appear as a normal outgoing bubble when the archive is updated.

## Project structure

```
chat_kiosk/
├── chat_kiosk.py      # main application
├── requirements.txt   # Python dependencies
└── .venv/             # virtual environment (not committed)
```
