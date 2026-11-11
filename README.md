# ChromiumUpdaterPy

A small cross-platform desktop app that checks for, downloads, and installs the
latest official Chromium snapshot builds, on your terms: no background service,
no scheduler, no bundled browser. Written in Python with Qt (QtPy, PyQt5 or PyQt6)
for macOS, Windows, and Linux.

> **Status:** early development. The window and settings work; checking,
> downloading, and installing are not implemented yet.

## Features

- One compact window: status, progress, Copy URL, and a single action button.
- Pick any target: macOS (ARM64, x64), Windows (ARM64, x64, x86), Linux (ARM64, x64).
- Download only, or also extract and install when the target is this machine.
- macOS: optional preparation that removes quarantine and repairs the app signature.
- Windows: EXE installer (optionally system-wide) or portable ZIP with a desktop shortcut.
- Proxy: system, direct, or manual host and port.
- Optional check on startup. Nothing is ever downloaded or installed automatically.

Planned: the update check, streaming downloads from Google's official
`chromium-browser-snapshots` storage, safe replace-with-rollback installation,
and standalone builds.

## Run

Needs Python 3.10 or later.

```sh
python3 -m pip install -r requirements.txt
python3 ChromiumUpdater.py
```

This installs QtPy and PyQt5. To use PyQt6 instead:

```sh
python3 -m pip install PyQt6
QT_API=pyqt6 python3 ChromiumUpdater.py
```

## Settings

Settings are saved in the system's native store (plist, registry, or
`~/.config`). Downloads go to the user cache folder by default. Default install
folders:

| System | Default install folder |
| --- | --- |
| macOS | `~/Applications` |
| Windows (ZIP) | `%LOCALAPPDATA%\ChromiumUpdater\ChromiumUpdaterPy\Chromium` |
| Linux | `~/.local/opt/chromium` |

The Windows and Linux defaults are provisional.

See [PLAN.md](PLAN.md) for the roadmap and the macOS signing policy.
