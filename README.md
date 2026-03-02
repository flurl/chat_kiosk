# Chat Kiosk

A fullscreen touchscreen chat UI for Raspberry Pi / Raspbian, built with [Kivy](https://kivy.org).

Reads messages from a Signal-format JSONL archive, watches for new ones in real time, and displays image attachments as a fullscreen slideshow.

## Features

- Dark-themed, fullscreen kiosk mode
- Chat bubbles — outgoing (right/blue) and incoming (left/grey)
- Handles message edits and deletes from the archive
- Tap any message with images to open a fullscreen slideshow
- Incoming messages with images automatically trigger the slideshow
- Scrollable message history, auto-scrolls to latest message
- Text input bar with Send button (stub — prints to console)
- Polls the JSONL file every second for new messages

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

| Constant            | Default                        | Description                              |
|---------------------|--------------------------------|------------------------------------------|
| `MESSAGES_FILE`     | path to `messages.jsonl`       | Full path to the JSONL archive file      |
| `ATTACHMENTS_DIR`   | `<archive dir>/attachments/`   | Directory containing attachment files    |
| `POLL_INTERVAL`     | `1.0`                          | Seconds between file-change checks       |
| `SLIDESHOW_INTERVAL`| `4.0`                          | Seconds per slide during auto-advance    |

## Running

```bash
.venv/bin/python chat_kiosk.py
```

To exit fullscreen during development, press `F11` or `Escape`.

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

Sending is currently a stub — the text is printed to stdout with a `[SEND]` prefix. Actual delivery (e.g. via Signal CLI or an HTTP API) will be wired in later.

## Project structure

```
chat_kiosk/
├── chat_kiosk.py      # main application
├── requirements.txt   # Python dependencies
└── .venv/             # virtual environment (not committed)
```
