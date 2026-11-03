# ChromiumUpdaterPy

A manual Chromium updater, developed in small, reviewable steps.

## Current step

The current step is a window with persistent platform settings: a top-level `Window(QWidget)` class and a guarded entry point
in `ChromiumUpdater.py`. All application code will stay in that single file.
Widget members and methods follow the Qt-style naming used in the owner's tools.
The `Platform` class owns OS/architecture detection, target names, matching rules,
macOS preparation eligibility, and platform-specific default folders/preferences.
`Settings` persists those defaults; `Window` uses the platform interface.

The compact layout has a dedicated status line above the progress bar: status on
the left, a target platform/architecture badge on the right. Copy URL sits beside
the progress bar, with Check for Updates, an action button, and Settings beneath it. The action
button will reflect the operation selected in settings and become Cancel while
working. There are no separate platform/version/action rows or diagnostics button.
Settings expands/collapses a group with Platform, download folder, extraction,
installation and its destination, macOS preparation, archive cleanup, and proxy
preferences (system, direct, or a manual HTTP host/port). Operation buttons remain
placeholders until their behavior is implemented. There is no
downloader, worker, extraction, installation, or signing implementation at this step.

Check for updates on startup is an opt-in preference, off by default. Enabling it
hides the main Check for Updates button and shows Manual refresh beside the option
in Settings. When auto-check is off, Manual refresh is hidden. Both manual buttons
share the same check action; the startup check itself will be connected
when the checking worker is implemented. It will not download or install anything
automatically or schedule background checks.

Status is an ordinary label, independent of the native progress bar's rendering.
The download target defaults to the detected OS/native CPU architecture when no
saved choice exists. Changing it saves the choice and updates the badge. Platform
choices are preferences; they do not imply a working downloader or installer yet.
An unrecognized host requires an explicit selection.
Selecting Auto saves an empty preference and resolves it to the detected concrete
platform for the badge and operation settings.

When the target differs from the detected platform/architecture, extraction,
installation, macOS preparation, and post-install cleanup are hidden and disabled. The action
becomes Download. Saved preferences remain intact for switching back. Download
folder and proxy controls still apply. Archive-based installation requires extraction; macOS
preparation requires a matching macOS target and extraction. Download cleanup will
run only after successful installation or Windows ZIP extraction, when those
operations are implemented. Settings are ordered Common, Specific, then Proxy;
inapplicable controls and their labels are hidden, including manual-proxy fields
outside Manual mode.
Linux defaults to download only. macOS preparation defaults to enabled and archive
cleanup defaults to disabled. The manual proxy UI has no password fields.
The Linux install default (`~/.local/opt/chromium`) and Windows ZIP destination
(`AppLocalDataLocation/Chromium`) are provisional custom choices pending platform
testing. Existing saved destinations take precedence over these defaults.

Platform-specific controls are visible only when the resolved target matches the
host: macOS preparation on a matching Mac; Windows package, all-users installation,
and desktop shortcut options on matching Windows. Auto resolves to the native
target. Hidden preferences remain saved and the window height follows visible rows.
Windows preferences offer EXE (default) or ZIP. With EXE selected, Install means running the installer. Its optional
all-users mode corresponds to `--system-level` and will require administrator
approval; extraction and custom destination are hidden. With ZIP selected,
Extract uses the chosen Install folder, and the redundant Install checkbox is
hidden. Optional desktop shortcut creation is available for ZIP extraction,
off by default. Cleanup applies after successful requested setup. Taskbar pinning
is deferred until development on a Windows machine. Only preferences and UI
behavior exist; no installer, extraction, or shortcut operation is implemented yet.

Settings uses `QSettings()` with the platform's native backend and the application's
organization/name. No INI filename or format is specified. Keys use
`Settings.Option`, a Python 3.10-compatible `Enum` with string values: use
`Settings.Option.PLATFORM.value` to obtain `"Platform"`. Values are stored as
strings and read with `type=str`.

## Run

With Python 3.10+, install the dependencies and run the source file directly:

```sh
python3 -m pip install -r requirements.txt
python3 ChromiumUpdater.py
```

QtPy handles binding selection. The application does not select or enforce a
binding. To use PyQt6 instead, install it and select it through QtPy:

```sh
python3 -m pip install PyQt6
QT_API=pyqt6 python3 ChromiumUpdater.py
```

Python 3.14.8, QtPy 2.4.3, PyQt5 5.15.11, and PyQt6 6.11.0 are available on the
development Mac; the documented Python minimum has not yet been tested separately.
PyInstaller packaging will be added later and does not require package metadata.

## Later choices

- Settings expands inside the window and collapses back, retaining values. The
  status row badge reflects the selected download target, rather than the host OS.
  The main action button reflects the configured operation and will switch to Cancel while
  working; no separate Cancel or Install button is planned.
- Add a top-level Worker class when background work is implemented. A
  `QObject` moved to a `QThread` supports queued slots and an event loop; a
  `QThread` subclass with `run()` is a simpler option for sequential blocking work,
  as used in the owner's Blackstar and Pyramid tools. Choose with the actual work.
- Keep network and filesystem operations outside `Window`, as functions or
  top-level classes in the same source file.

See [PLAN.md](PLAN.md) for the full roadmap and macOS signing policy.
