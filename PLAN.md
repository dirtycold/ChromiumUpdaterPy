# ChromiumUpdaterPy: implementation and handoff plan

Status: window scaffold and persistent settings UI; downloader and workers deferred.
Date: 2026-11-03.
Intended repository: `~/Projects/ChromiumUpdaterPy`.
Reference project: `~/Projects/ChromiumUpdaterQt`.

This document now lives in the intended ChromiumUpdaterPy repository. It is self-contained; the implementing session does not need the planning conversation.

## Goal and agreed scope

Build a compact desktop application that manually checks for, downloads, and installs official Chromium snapshots. Preserve the spirit of the owner's old Qt updater while improving responsiveness, error handling, and installation reliability.

- Pure Python application code. No project-maintained C++ or C++ compilation step. Qt and Python bindings contain native code internally.
- All Qt imports go through `qtpy`.
- Prefer **PyQt5** for installation guidance and standalone release bundles, without enforcing it in application code. The owner prefers its smaller binaries; measure actual artifact sizes rather than promising a saving.
- Let QtPy handle binding selection for source installations. Do not add application-level binding selection or enforcement. Users can optionally set `QT_API` themselves; PyQt5 remains the intended standalone packaging binding.
- Checks are manual by default, with an optional startup check (`AutoCheck`, off by default). Enabling it hides Check for Updates. Wire the actual startup check when the worker is implemented. No automatic downloads or installation, LaunchAgent, scheduled checks, daemon, or background updater. Earlier twice-weekly automation proposals are superseded.
- Platform order: implement and validate macOS first on this machine, then Windows, and eventually Linux usability. Linux should default to download only because the owner uses Flatpak Chromium. Extraction and installation are explicit optional actions where implemented; Linux work is deferred until after Windows.
- Download upstream binaries directly. Do not compile Chromium, maintain a binary mirror, or build a Homebrew cask.
- Release packaging runs **only when a version tag such as `v0.1.0` is pushed**. No scheduled, ordinary-commit, or pull-request packaging.
- The owner will configure the repository's personal Git identity. Do not initialize Git, change Git identity, configure remotes, commit, push, or publish without a subsequent instruction.

## Reference application findings

Read the old repository's `README.md`, `chromiumupdater.hpp`, and `chromiumupdaterwidget.cpp` before implementation.

The old UI offers Check Version, Download and Install, progress, Copy URL, saved settings, proxy configuration, and optional archive cleanup. It defaults to HTTPS and uses asynchronous QNetworkAccessManager and QProcess; it is not limited to HTTP. Its weaknesses include buffering the entire installer in memory, limited network/error validation, and tightly coupled UI/install state.

Keep its compact workflow, not its limitations. Do not copy the old Windows MSVC manifest into the new source tree. Configure any manifest needed by a packaged executable through the packaging tool.

## User experience

Keep a small window with:

- No separate Platform, Installed, Available, or Action rows. Show relevant version/revision results through status as those behaviors are implemented.
- Check for Updates and one action button whose label reflects the operation selected in settings. That same button becomes Cancel while an operation is active; there is no separate Cancel button. Allow download only; extraction and installation must be explicitly selectable rather than unexpected consequences of downloading.
- A dedicated status row sits above the progress bar: status (including Ready) on the left, the target platform/architecture badge right-aligned. Status remains visible independently of native progress rendering, including in busy mode. Copy URL sits beside the progress bar. Use a normal progress bar; custom progress painting is unnecessary for status visibility.
- The badge describes the selected download target, not the host OS. Selection belongs in settings and defaults to the detected OS/native CPU architecture only when no saved selection exists.
- Compact settings for extraction/installation choices, destination, download-cache cleanup, proxy preferences, and macOS preparation.
- No dedicated Copy Diagnostics button. Retain command/error output internally as required by later operations; decide how to expose troubleshooting details when needed.

Settings expands within the main window as one Settings group, increasing its size when opened and shrinking back when collapsed. It contains Platform, download folder, extraction, installation and its destination, macOS preparation, post-install archive cleanup, and proxy preferences (system/direct/manual HTTP host and port). Collapsing settings retains values. The action button reflects the configured operation. When the selected target does not match the detected OS/architecture, disable extraction, installation, preparation, and post-install cleanup and make the effective action download only; preserve saved preferences. Download folder and proxy preferences remain available. Installation requires extraction; preparation is available only for matching macOS targets with extraction. Linux defaults to download only, macOS preparation defaults on, and cleanup defaults off. These are UI preferences; operation implementations remain deferred.

On macOS, expose signing/postprocessing as an option enabled by default. Explain that it removes quarantine and conditionally repairs only the outer signature for the known error described below. Disabling it skips those modifications, not ordinary validation; report verification failures clearly without silently enabling repair.

Check when requested manually or on startup when the user has enabled AutoCheck. Do not automatically download, install, launch Chromium, or close browser processes. If Chromium is running, explain that installation must wait; the user closes it and retries manually. Retain a validated download for that retry according to the cache setting.

Keep the interface responsive during network, extraction, signature checks, and filesystem work. Cancellation must leave the installed app usable. Disable cancellation during the short replacement/rollback critical section and display the current phase.

## Architecture

The owner has superseded the original multi-module proposal: keep all application code in one source file, `ChromiumUpdater.py`, alongside `requirements.txt`. Use requirements.txt for dependencies; package metadata is unnecessary for direct source execution and later PyInstaller bundling. Do not split code into platform modules or a `src/` package. Separate responsibilities with functions and top-level classes inside that file. Current structure:

```text
ChromiumUpdater.py       # top-level Window(QWidget), guarded entry point; operations added later
requirements.txt
README.md
PLAN.md
```

Follow the direct Qt-style structure and naming in the owner's Blackstar, Pyramid, and RTTViewer tools. `Window` inherits `QWidget` and is defined at module scope. Any future `Worker` is also a top-level class, never nested inside a window factory. Keep this proportional to a small application. Operation logic should be testable without constructing a GUI. Blocking work runs outside the GUI thread and reports through qtpy signals; widgets stay on the GUI thread. Use subprocess argument lists, not interpolated shell commands.

The top-level `Platform` class owns all platform-specific detection and decisions, including OS/architecture, target names/matching, macOS preparation eligibility, and platform-specific default folders/preferences. `Window` and `Settings` use this interface rather than detecting or branching on the host themselves. Keep the class in the same application source file.

Do not explicitly raise exceptions in application code. Catch recoverable exceptions locally and use fallback values or explicit failure results; future workers must report errors through signals/status instead of allowing exceptions to escape.

Use explicit states to prevent overlapping operations and a process-level installation lock to prevent simultaneous replacement by two updater instances. Persist the installed revision only after successful installation. Reconcile saved metadata with the installed app when possible, including a pre-existing installation not managed by this tool. Distinguish snapshot revisions from Chromium's displayed version number.

Use standard per-user configuration/cache/log locations rather than files beside the executable. Settings uses native `QSettings()` with no explicit INI format or path, following RTTViewer's get/set wrapper. Keys are named `Enum` members with string `.value` keys; stored values are strings, with explicit typed reads/conversions as needed. Require Python 3.10 compatibility for Ubuntu 22.04; do not use Python 3.11-only `StrEnum`. Native Qt settings storage remains platform-dependent.

## Official downloads

Use Google's official `chromium-browser-snapshots` storage over HTTPS with certificate verification. Detect host OS and native architecture; account for processes running under emulation where relevant.

On macOS, use `Mac_Arm` or `Mac`, the corresponding `LAST_CHANGE`, and revision-specific `chrome-mac.zip`. Capture the chosen revision once so a changing latest pointer cannot mix metadata and downloads. Validate revision responses as positive integers. Never silently downgrade.

Windows artifact inspection confirmed both `chrome-win.zip` and `mini_installer.exe` in current `Win`, `Win_x64`, and `Win_Arm64` snapshots. Offer both through the package preference. Recheck revision-specific availability and validate behavior when implementing Windows operations.

Stream downloads into partial files. Support bounded timeouts/retries, cancellation, HTTP errors, truncated-download detection, cleanup, and indeterminate progress when length is unknown. Promote a file to reusable cache only after validation; filename existence is insufficient.

Compute SHA-256 for diagnostics and cache bookkeeping. A locally computed hash is not independent proof of authenticity. Verify against trusted upstream integrity metadata if available; otherwise accurately document HTTPS as the source-authentication mechanism. Do not invent published checksums.

Use one explicit proxy mode: system, direct, or manual. Verify the chosen networking library's actual system-proxy behavior on each OS. Avoid storing proxy passwords in plain text. Authenticated-proxy support can be deferred if it materially expands the initial implementation.

## Installation reliability

- Validate expected app structure and architecture before replacement.
- Check destination permissions and available space. Offer a user-writable destination or report a clear error; do not run the GUI as root or add automatic elevation in the first implementation.
- Extract into staging while preserving executable permissions and legitimate framework symlinks. Reject unsafe paths and symlinks escaping staging. Evaluate native `ditto` on macOS instead of assuming Python ZIP extraction preserves bundles correctly.
- Prepare and verify the staged candidate before touching the installed app.
- Detect Chromium and relevant helper processes before replacement; never force-quit. Recheck immediately before replacing and acknowledge the residual launch race.
- Stage on the destination filesystem for reliable rename behavior. Keep one previous build for rollback. A sequence of renames is not fully atomic; handle failed replacement and recovery after interruption explicitly.
- Verify the installed result and roll back if replacement/final verification fails. Record installed metadata only on success.
- Preserve user profiles, preferences, passwords, SIP, browser sandbox settings, and macOS privacy databases.

Do not equate signature verification with proof of working local-network access. Do not launch the browser automatically to test an installed build.

## macOS signing: exact required SOP

The owner reports that an official snapshot launched after quarantine removal but could not access the LAN with `NetworkServiceSandbox` enabled. Repairing **only the outer application's signature** restored local-network access. Treat this as an observed mitigation on the owner's machine, not a confirmed upstream fix or a guarantee on every macOS release.

When macOS preparation is enabled (the default), prepare the staged app using this exact decision tree, then verify the installed result. When disabled, preserve quarantine and signatures, still perform shallow strict verification, and stop installation on failure without repair:

1. Remove only `com.apple.quarantine` recursively, preserving unrelated attributes. Missing quarantine is harmless; genuine permission or I/O failures are not.
2. Run shallow, strict verification:

   ```sh
   codesign --verify --strict --verbose=2 /path/to/Chromium.app
   ```

3. On success, **preserve the existing signature**, whether ad-hoc or certificate-based.
4. Only if verification fails with this diagnostic:

   ```text
   code has no resources but signature indicates they must be present
   ```

   repair the outer app exactly once:

   ```sh
   codesign --force --sign - /path/to/Chromium.app
   ```

5. Repeat the same shallow strict verification. Stop on failure. For any other initial verification error, stop without re-signing.

Do not add `--deep` to signing or use recursive verification as the acceptance criterion for this SOP. Do not rewrite nested helper signatures or entitlements. Do not use `--no-sandbox`, disable NetworkServiceSandbox, reset Local Network Privacy, or disable SIP. Stabilize the diagnostic locale if needed and retain full command output for troubleshooting.

Keep the app name and `org.chromium.Chromium` bundle ID. Ad-hoc signing can affect identity-based privacy decisions across revisions; do not promise permission persistence. The final manual test is a normal launch and access to the owner's usual LAN address with NetworkServiceSandbox enabled, allowing a normal macOS prompt if shown.

Planning inspection found Chromium 159.0.8138.0 installed on the owner's Mac with an ad-hoc outer signature and a linker-signed framework. Recursive verification failed on the framework. That finding does **not** justify deviating from the successful outer-only SOP. Do not modify the current installation merely to reproduce it.

## Windows signing and installation

The settings UI now offers EXE or ZIP, with EXE as the default. For matching
Windows targets, EXE installation runs the upstream installer with an optional
all-users preference corresponding to `--system-level` (not `--system`). The
installer owns the destination; hide manual extraction and install-folder
editing. ZIP extraction uses the specified destination and has optional desktop
shortcut creation, off by default; the separate Install checkbox is unnecessary
for this route and is hidden. Show
platform-specific controls only when the resolved target matches the native host:
macOS preparation on matching Macs; package, system-wide installation, and desktop
shortcut options on matching Windows. Hide their labels too and adjust window
height while retaining fixed width. Save all preferences as strings, preserving
hidden and disabled choices. Foreign targets remain download only.
Order settings Common, Specific, then Proxy. Hide inapplicable controls and their
labels, including prerequisite-dependent operations and nonmanual proxy fields.
These are options only: no Windows operations have been implemented. Taskbar
pinning has no settings option for now; revisit it when developing on a Windows
machine.

Windows generally does not require a local signature repair equivalent to this macOS procedure. Preserve upstream executable contents and signatures. SmartScreen, Smart App Control, and organizational policy may warn about or block applications; local self-signing is not a general remedy. Do not automatically unblock files, alter security policies, or self-sign Chromium.

Choose the install location, archive-versus-installer strategy, architecture mapping, process detection, and rollback details after verifying current official artifacts. Validate a real Windows install/update before claiming support; CI tests alone do not establish interactive desktop behavior.

## Packaging and GitHub Actions

Support direct source execution with Python, qtpy, and PyQt5/PyQt6. Use PyInstaller for standalone releases with **PyQt5**. Build in clean environments containing only the chosen binding so both bindings are not accidentally bundled.

Release trigger:

```yaml
on:
  push:
    tags:
      - 'v*'
```

Validate tags as release versions. No scheduled builds, ordinary-commit packaging, or pull-request packaging. Run relevant tests within tagged workflows and document local checks to perform before tagging.

PyInstaller is not a cross-compiler. GitHub-hosted runners can perform platform builds so the owner does not need local machines/VMs for each target. Verify current runner labels, Python versions, and PyQt5 wheel availability before committing to a matrix. Proposed targets:

| Platform | Artifact | Condition |
| --- | --- | --- |
| macOS Apple Silicon | `.app` in ZIP | Initial target; confirm compatible dependencies |
| macOS Intel | `.app` in ZIP | Confirm runner and wheel availability |
| Windows x64 | Portable updater ZIP | Validate Windows installer first |
| Linux | Deferred | Shared code remains portable |

Smoke-test each packaged updater on its corresponding runner. Publish artifacts and SHA-256 files to a GitHub Release only after required jobs succeed. Limit release write permissions to the publishing job. Adding the workflow does not itself authorize creating a tag or publishing a release.

Signing the updater is separate from preparing Chromium. Validate PyInstaller's macOS signing behavior for the chosen target. Without Developer ID/notarization, downloaded updater bundles may encounter Gatekeeper; document their actual status. Windows updater bundles can encounter SmartScreen. Paid signing identities/certificates have not been requested.

## Implementation phases and acceptance criteria

Work gradually in small, reviewable increments. The owner prioritizes code correctness, clarity, and cleanliness over speed or feature breadth. Keep abstractions proportional to demonstrated needs, avoid speculative infrastructure, and review each increment with focused tests before expanding its scope. These phases describe the roadmap, not an instruction to implement everything in one pass.

The window and main entry point are in place; the next requested increment adds native persistent platform selection and an expandable settings panel. The previous downloader implementation was removed at the owner's request to start over. Do not add workers or operation behavior until the next requested increment. Discuss options as work progresses. Do not validate or run tests after each edit; defer validation/tests to a future pre-commit hook, which is not yet implemented.

### 1. Foundation and shared engine

Maintain the dependency list and implement configuration/cache handling, revision discovery, streaming downloads, operation states, locking, and logs. Inspect the owner's Python/Qt environment first.

Acceptance: meaningful tests cover invalid metadata, HTTP failures, cancellation, incomplete downloads, cache validation, installed-revision handling, and overlapping operations. No GUI is needed to test shared logic.

### 2. Compact qtpy GUI

Implement manual checking, the settings-driven action/Cancel button, a dedicated status row with target badge above progress, Copy URL, and target selection in settings. Do not add a Copy Diagnostics button. Let QtPy handle binding selection without application-level selection code.

Acceptance: the window remains responsive during long operations; failure/retry leaves it usable; GUI smoke tests pass with both bindings.

### 3. macOS installation

Implement faithful extraction, architecture validation, quarantine handling, the exact signature decision tree, staged replacement, backup, and rollback/recovery.

Acceptance: test preserved valid signatures; exactly one outer repair for the known error; refusal of unrelated errors; repair failure; absent quarantine; disabled preparation preserving quarantine/signatures while still validating; running Chromium; failed replacement; and interrupted-operation recovery. Verify that nested helper signatures remain unchanged. Validate an isolated real official snapshot before replacing the owner's app. Record whether the final manual LAN-access test was completed.

### 4. Windows installation

Verify upstream artifacts and implement the platform installer without coupling shared code to macOS commands.

Acceptance: Windows testing and a real installation exercise cover install/update, running-process handling, rollback, and launching the resulting browser. Clearly label any behavior not yet validated.

### 5. Tag-triggered releases and documentation

Add PyInstaller configuration and version-tag-only workflows. Document source execution, binding selection, installation location, proxy/cache settings, signing behavior, recovery, and release steps.

Acceptance: no C++ compilation is needed to run source; bundled apps contain required Qt/TLS plugins and launch on target runners; ordinary commits cause no packaging; a requested tagged release produces the intended artifacts and checksums.

### Later: Linux usability

After macOS and Windows, add Linux download-only usability using verified official artifacts. Offer extraction and installation only as explicit choices when implemented and validated. Do not modify the owner's Flatpak installation. Linux packaging and installation details remain deferred.

## Handoff instructions

Implement in `~/Projects/ChromiumUpdaterPy`, not in the temporary Homebrew workspace. Respect the owner's pending personal Git identity setup. No application implementation or new-repository Git operations occurred during planning.

Resolve routine implementation details from the environment and document assumptions. Ask only when a choice materially changes scope or user behavior. Preserve the exact outer-only signing policy unless the owner explicitly agrees to change it.

Before declaring completion, document actual supported OS/architecture/Python combinations, verification performed, remaining platform limitations, and whether real LAN access was tested. Never claim tests passed merely because mocks passed.

Abandoned tap drafts in `~/Projects/homebrew-chromium` are outside this project's scope. Do not use their signing or scheduling proposals as requirements and do not delete them without instruction.

## References

- Previous source: `~/Projects/ChromiumUpdaterQt`.
- Official Chromium downloads: https://www.chromium.org/getting-involved/download-chromium/
- QtPy and binding selection: https://github.com/spyder-ide/qtpy
- PyInstaller and platform builds: https://pyinstaller.org/en/stable/
- GitHub-hosted runners: https://docs.github.com/en/actions/reference/runners/github-hosted-runners
- Microsoft SmartScreen: https://learn.microsoft.com/en-us/windows/security/operating-system-security/virus-and-threat-protection/microsoft-defender-smartscreen/

The owner's successful macOS SOP is reproduced above so the inaccessible shared-chat link is not needed for implementation.
