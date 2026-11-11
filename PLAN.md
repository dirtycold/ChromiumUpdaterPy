# ChromiumUpdaterPy plan

Reference project: `~/Projects/ChromiumUpdaterQt` (keep its compact workflow, not its limitations).

## Status

Done: window, entry point, icon, Windows app ID, persistent typed settings, grouped settings panel, update check, segmented download with MD5 verification and retry until cancelled.
Not started: extraction, installation, signing, packaging.

Next:

- Proxy variables: in System mode, use `https_proxy`/`HTTPS_PROXY`, then `http_proxy`, when set; Qt only reads `http_proxy`, and only on Linux. No `no_proxy`.
- Chunk queue: split the file into 4 MB chunks (1 MB on slow lines) and keep the connections busy taking the next chunk, so one slow connection no longer drags the end. Check first whether Qt 6's HTTP/2 puts all connections on one TCP connection.
- "Remove download" applies only when extracting. Decide the Windows EXE case (hide it, or keep it for the installer) during the Windows work; for now it stays enabled there once the installer runs.

## Scope

- Manually check for, download, and install official Chromium snapshots.
- Pure Python, one source file `ChromiumUpdater.py`, all Qt imports through `qtpy`.
- PyQt5 preferred for docs and release bundles; QtPy selects the binding, the app never enforces one.
- Python 3.10 compatible (Ubuntu 22.04): no `StrEnum`.
- Optional check on startup (`AutoCheck`, off by default). Never download, install, launch Chromium, or close browser processes automatically. No scheduler, daemon, or LaunchAgent.
- Platform order: macOS, then Windows, then Linux. Linux defaults to download only (the owner uses Flatpak Chromium; never touch it).
- Download upstream binaries directly. No compiling Chromium, mirror, or Homebrew cask.
- Work in small, reviewable increments. Correctness and clarity over speed and breadth. No speculative infrastructure.

## Window

- Status row: status text on the left, target badge on the right. The badge shows the selected download target, not the host.
- Progress bar with Copy URL beside it.
- Check for Updates, one action button, Settings. The action button's label follows the settings ("Download", "Download and Extract", "Download and Install") and becomes Cancel while working.
- No separate version rows, Cancel button, or diagnostics button. Keep command and error output internally.
- Fixed width. The height follows the layout on every layout change.

## Settings panel

Three group boxes, expanding inside the window:

- **General:** Platform, download folder, check on startup. While that is on, Manual refresh shows here and Check for Updates is hidden.
- **This system** (titled macOS, Windows, or Linux); only this system's options are built.
  - macOS and Linux: extract, install, install folder, remove download; macOS adds Prepare.
  - Windows: package, then the EXE section (run installer, system-wide) or the ZIP section (extract, install folder, desktop shortcut), then remove download.
- **Proxy:** mode; host and port only in Manual mode.

Rules:

- Show everything that applies to the selected target. Disable, not hide, an option whose prerequisite is not ticked.
- Hide only what does not apply: the system box for a target that is not this machine (download only), the unselected Windows package section, nonmanual proxy fields.
- Qt 5 forms keep the spacing of hidden rows, so anything that hides is its own form section in a vertical box. All labels share one width.
- Folder fields are read-only and change through Browse.
- Hidden and disabled choices keep their saved values.
- Defaults: extract and install on for macOS and Windows, off for Linux; Prepare on; remove download off; EXE package; system proxy.

## Architecture

- Top-level classes in the one file: `Platform`, `Proxy`, `Settings`, `Window(QWidget)`; a future `Worker` is top-level too. Follow the naming in the owner's Blackstar, Pyramid, and RTTViewer tools.
- `Platform` owns all OS and architecture detection, target names, and default folders. `Window` and `Settings` do not branch on the host themselves.
- `Settings` wraps native `QSettings()`. Keys are `Enum` members with string values. Typed defaults live in `Settings`; a value is written only when changed.
- Organization `ChromiumUpdater`, application `ChromiumUpdaterPy`. Changing either moves the settings and the default folders.
- Blocking work runs off the GUI thread and reports through signals. Operation logic is testable without a GUI.
- Do not raise exceptions in application code. Catch locally; workers report failures through signals and status.
- Subprocesses take argument lists, never shell strings.
- Explicit operation states prevent overlap; a process-level lock prevents two updaters replacing the app at once.
- Record the installed revision only after a successful install. Reconcile with an existing install not made by this tool. Keep snapshot revision and Chromium version number apart.

## Downloads

- Google's official `chromium-browser-snapshots` storage over HTTPS with certificate verification.
- macOS: `Mac_Arm` or `Mac`, `LAST_CHANGE`, then the revision's `chrome-mac.zip`. Windows: `Win`, `Win_x64`, `Win_Arm64` carry both `chrome-win.zip` and `mini_installer.exe`; recheck when implementing.
- Capture the revision once per operation. Validate it as a positive integer. Never downgrade silently.
- Stream into a partial file. Handle timeouts, bounded retries, cancellation, HTTP errors, truncation, and unknown length.
- A file becomes reusable cache only after validation.
- SHA-256 is for diagnostics and cache bookkeeping, not proof of authenticity. Do not invent published checksums.
- Proxy modes: system, direct, manual. Verify real system-proxy behaviour per OS. No stored proxy passwords; authenticated proxies are deferred.

## Installation

- Check structure, architecture, destination permissions, and free space first. No root, no automatic elevation.
- Extract into temporary staging, keeping permissions and framework symlinks. Reject unsafe paths and escaping symlinks. Evaluate `ditto` on macOS.
- Prepare and verify the staged copy before touching the installed app.
- Then move it into the install folder: `Chromium.app` on macOS, the extracted folder on Linux and for the Windows ZIP. Nothing extracted is left behind, so there is no "remove extracted" option; delete the emptied wrapper folder.
- Replacing: rename the old copy aside, move the new one in, delete the old one only after final verification, put it back on failure. Keep one previous build. Handle recovery after interruption.
- Stage on the destination volume where possible; across volumes a move is copy-then-delete.
- Chromium must not be running. Detect it, never force-quit, tell the user to close it and retry. Recheck right before replacing.
- "Remove download" is enabled once Extract is ticked and runs only after the requested steps succeed.
- Cancellation leaves the installed app usable; it is disabled during the short replace and rollback section.
- Never touch profiles, preferences, passwords, SIP, sandbox settings, or privacy databases. Never launch the browser to test.

## macOS signing

Observed on the owner's Mac: an official snapshot could not reach the LAN with `NetworkServiceSandbox` enabled until **only the outer app signature** was repaired. This is a mitigation, not a confirmed upstream fix.

With Prepare on (the default), on the staged app:

1. Remove only `com.apple.quarantine`, recursively. Missing quarantine is fine; permission or I/O errors are not.
2. Verify, shallow and strict:

   ```sh
   codesign --verify --strict --verbose=2 /path/to/Chromium.app
   ```

3. On success, keep the existing signature.
4. Only if it fails with exactly

   ```text
   code has no resources but signature indicates they must be present
   ```

   repair the outer app once:

   ```sh
   codesign --force --sign - /path/to/Chromium.app
   ```

5. Verify again the same way. Stop on failure. Stop without re-signing on any other error.

With Prepare off: keep quarantine and signatures, still run step 2, and stop on failure.

Never: `--deep`, recursive verification as the criterion, re-signing nested helpers, `--no-sandbox`, disabling NetworkServiceSandbox, resetting Local Network Privacy, disabling SIP. Keep the app name and the `org.chromium.Chromium` bundle ID. Keep full command output.

Final manual test: launch normally and reach the usual LAN address. Record whether it was done. A passing signature check is not proof of LAN access.

## Windows

- EXE (default): run the upstream installer; the system-wide option passes `--system-level`. The installer owns the destination.
- ZIP: extract, then move to the install folder; optional desktop shortcut, off by default.
- From source the process sets `SetCurrentProcessExplicitAppUserModelID("ChromiumUpdater.ChromiumUpdaterPy")` before any window exists; otherwise the taskbar shows Python's icon. Verify on Windows 11.
- No signature repair, unblocking, policy changes, or self-signing. SmartScreen may warn.
- Taskbar pinning is deferred. Nothing counts as supported before a real install and update on Windows.

## Packaging

- PyInstaller with PyQt5, built in a clean environment holding one binding only.
- Build only when a version tag (`v*`) is pushed. No scheduled, per-commit, or pull-request builds. Adding the workflow does not authorize tagging or publishing.
- Targets: macOS Apple Silicon and Intel (`.app` in ZIP), Windows x64 (portable ZIP). Linux deferred. Check runner labels and PyQt5 wheels first.
- Smoke-test each bundle on its runner. Publish artifacts and SHA-256 files only after all jobs pass.
- Icons: `icon.svg` is the source and the window icon, loaded through `resource()` and bundled as data. Add QtSvg as a hidden import, or the icon loads empty. `icon.icns` is the macOS bundle icon; build `icon.ico` during the Windows work.
- macOS bundle: set `UIDesignRequiresCompatibility` through `BUNDLE(info_plist=...)` to keep the pre-Tahoe look with Qt 5. Apple may drop the key later.
- The updater itself is unsigned: expect Gatekeeper and SmartScreen warnings and document them.

## Phases

1. **Engine:** revision check, streaming download, cache, states, lock, logs. Tests cover bad metadata, HTTP failures, cancellation, truncated downloads, cache validation, and overlap, without a GUI.
2. **GUI wiring:** check, action and Cancel, progress, Copy URL. The window stays responsive and usable after failure; smoke-tested with both bindings.
3. **macOS install:** extraction, validation, the signing steps above, replace and rollback. Test each branch of the signing steps, running Chromium, failed replacement, and interrupted recovery. Try an isolated real snapshot before replacing the owner's app.
4. **Windows install:** both package routes, tested on a real machine.
5. **Releases:** PyInstaller configuration, tag-only workflow, user documentation.
6. **Linux:** download only first; extract and install once validated.

Never claim tests passed because mocks passed. Validation and tests are deferred to a future pre-commit hook, not run after each edit.

## References

- Chromium downloads: https://www.chromium.org/getting-involved/download-chromium/
- QtPy: https://github.com/spyder-ide/qtpy
- PyInstaller: https://pyinstaller.org/en/stable/
- GitHub-hosted runners: https://docs.github.com/en/actions/reference/runners/github-hosted-runners
- SmartScreen: https://learn.microsoft.com/en-us/windows/security/operating-system-security/virus-and-threat-protection/microsoft-defender-smartscreen/
