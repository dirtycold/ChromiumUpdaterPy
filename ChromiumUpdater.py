"""Chromium Updater: a compact Qt window, built one step at a time."""

import base64
import hashlib
import os
import sys
from enum import Enum
from pathlib import Path

from qtpy.QtCore    import  QDir, QElapsedTimer, QEvent, QFile, QFileInfo, QIODevice, QObject, QSettings, \
                            QStandardPaths, QSysInfo, QThread, QTimer, QUrl, Qt, Signal, Slot
from qtpy.QtGui     import  QIcon
from qtpy.QtNetwork import  QNetworkAccessManager, QNetworkProxy, QNetworkProxyFactory, \
                            QNetworkReply, QNetworkRequest
from qtpy.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, 
                            QFormLayout, QGroupBox, QHBoxLayout, QLabel, 
                            QLayout, QLineEdit, QProgressBar, QPushButton, 
                            QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
                            )


def resource(name):
    """Bundled file path: PyInstaller's unpack folder when frozen, else next to this script."""
    return str(Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name)


class Platform:
    class Directory(Enum):
        DOWNLOAD = 0
        INSTALL  = 1

    class Package(Enum):
        EXE = 0
        ZIP = 1

    def __init__(self):
        self.os = {"darwin": "macos", "win32": "windows", "linux": "linux"}.get(sys.platform, "")
        self.arch = {
            "arm64": "arm64", "aarch64": "arm64",
            "x86_64": "x64", "amd64": "x64",
            "i386": "x86", "i686": "x86", "x86": "x86",
        }.get(QSysInfo.currentCpuArchitecture().lower(), "")

    @staticmethod
    def supported():
        return {
            "macos-arm64":      "macOS · ARM64",
            "macos-x64":        "macOS · x64",
            "windows-arm64":    "Windows · ARM64",
            "windows-x64":      "Windows · x64",
            "windows-x86":      "Windows · x86",
            "linux-arm64":      "Linux · ARM64",
            "linux-x64":        "Linux · x64",
        }

    def system(self):
        return self.os

    def current(self):
        target = f"{self.os}-{self.arch}"
        return target if target in Platform.supported() else ""

    def native(self, target):
        return bool(target) and target == self.current()

    def directory(self, kind: Directory):
        if kind == self.Directory.DOWNLOAD:
            return QStandardPaths.writableLocation(QStandardPaths.CacheLocation)
        if kind != self.Directory.INSTALL:
            return ""
        return {
            "macos":    QStandardPaths.writableLocation(QStandardPaths.ApplicationsLocation),
            "windows":  QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation) + "/Chromium",
            "linux":    QStandardPaths.writableLocation(QStandardPaths.HomeLocation) + "/.local/opt/chromium",
        }.get(self.os, "")

    @staticmethod
    def remote(target, package):
        """Snapshot folder and file name on the server; no folder when there is no snapshot."""
        folder = {
            "macos-arm64":      "Mac_Arm",
            "macos-x64":        "Mac",
            "windows-arm64":    "Win_Arm64",
            "windows-x64":      "Win_x64",
            "windows-x86":      "Win",
            "linux-x64":        "Linux_x64",
        }.get(target, "")
        if target.startswith("windows"):
            return folder, "mini_installer.exe" if package == Platform.Package.EXE.name else "chrome-win.zip"
        return folder, "chrome-mac.zip" if target.startswith("macos") else "chrome-linux.zip"


class Proxy:
    class Mode(Enum):
        DIRECT = 0
        SYSTEM = 1
        MANUAL = 2


class Settings:
    class Option(Enum):
        PLATFORM    = "Platform"
        CHECK       = "Check"
        EXTRACT     = "Extract"
        INSTALL     = "Install"
        CLEANUP     = "Cleanup"
        DOWNLOADDIR = "Directory/Download"
        INSTALLDIR  = "Directory/Install"
        MACSIGN     = "Mac/Sign"
        WINPACKAGE  = "Win/Package"
        WINSYSTEM   = "Win/System"
        WINDESKTOP  = "Win/Desktop"
        PROXYMODE   = "Proxy/Mode"
        PROXYHOST   = "Proxy/Host"
        PROXYPORT   = "Proxy/Port"
        SEGMENTS    = "Segments"

    def __init__(self, platform: Platform):
        self.setting = QSettings()
        self.defaults = {
            self.Option.PLATFORM:       platform.current(),
            self.Option.CHECK:          False,
            self.Option.SEGMENTS:       4,
            self.Option.EXTRACT:        platform.system() in {"macos", "windows"},
            self.Option.INSTALL:        platform.system() in {"macos", "windows"},
            self.Option.CLEANUP:        2,
            self.Option.DOWNLOADDIR:    platform.directory(Platform.Directory.DOWNLOAD),
            self.Option.INSTALLDIR:     platform.directory(Platform.Directory.INSTALL),
            self.Option.MACSIGN:        True,
            self.Option.WINPACKAGE:     Platform.Package.EXE.name,
            self.Option.WINSYSTEM:      False,
            self.Option.WINDESKTOP:     False,
            self.Option.PROXYMODE:      Proxy.Mode.SYSTEM.name,
            self.Option.PROXYHOST:      "",
            self.Option.PROXYPORT:      8080,
        }

    def get(self, option: Option):
        default = self.defaults[option]
        try:
            return self.setting.value(option.value, default, type(default))
        except TypeError:
            return default

    def set(self, option: Option, value):
        self.setting.setValue(option.value, type(self.defaults[option])(value))


class Downloader(QObject):
    """Lives in its own thread; everything crossing over goes through a signal."""
    SERVER = "https://storage.googleapis.com/chromium-browser-snapshots"
    # Milliseconds between progress reports
    INTERVAL = 200
    # Milliseconds without any data before a request gives up
    TIMEOUT = 15000
    # Milliseconds before a dropped segment is requested again; it retries until cancelled
    DELAY = 2000
    # Errors a bad or lost network gives; a transfer timeout ends as OperationCanceledError, like a cancel does
    RETRIABLE = (
        QNetworkReply.NetworkError.ConnectionRefusedError,
        QNetworkReply.NetworkError.RemoteHostClosedError,
        QNetworkReply.NetworkError.HostNotFoundError,
        QNetworkReply.NetworkError.TimeoutError,
        QNetworkReply.NetworkError.OperationCanceledError,
        QNetworkReply.NetworkError.TemporaryNetworkFailureError,
        QNetworkReply.NetworkError.UnknownNetworkError,
        QNetworkReply.NetworkError.ProxyConnectionClosedError,
        QNetworkReply.NetworkError.ProxyTimeoutError,
    )

    class Result(Enum):
        DONE      = 0
        FAILED    = 1
        CANCELLED = 2

    checked = Signal(str, str, int)
    progress = Signal(int, int)
    # A Result, and the reason when it failed
    finished = Signal(object, str)

    def __init__(self):
        super().__init__()
        QNetworkProxyFactory.setUseSystemConfiguration(True)
        self.manager = QNetworkAccessManager(self)
        self.replies = []
        self.cancelled = False
        self.error = ""

    def request(self, url):
        request = QNetworkRequest(QUrl(url))
        request.setTransferTimeout(self.TIMEOUT)
        request.setAttribute(QNetworkRequest.Attribute.Http2AllowedAttribute, False)
        request.setRawHeader(b"Accept-Encoding", b"identity")
        return request

    @Slot(str, str, int)
    def proxy(self, mode, host, port):
        self.error = ""
        proxy = {
            Proxy.Mode.DIRECT.name: QNetworkProxy(QNetworkProxy.ProxyType.NoProxy),
            Proxy.Mode.MANUAL.name: QNetworkProxy(QNetworkProxy.ProxyType.HttpProxy, host, port),
        }.get(mode, QNetworkProxy(QNetworkProxy.ProxyType.DefaultProxy))
        if mode == Proxy.Mode.SYSTEM.name:
            value = next((os.environ[key].strip() for key in
                          ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY")
                          if os.environ.get(key, "").strip()), "")
            if value:
                url = QUrl(value)
                kind = {"http": QNetworkProxy.ProxyType.HttpProxy,
                        "socks5": QNetworkProxy.ProxyType.Socks5Proxy}.get(url.scheme().lower())
                port = url.port(1080 if url.scheme().lower() == "socks5" else 80)
                if not url.isValid() or not url.host() or kind is None or not 1 <= port <= 65535:
                    self.error = "Invalid proxy environment URL; use http://host:port or socks5://host:port"
                elif url.userInfo():
                    self.error = "Authenticated proxies are not supported yet"
                else:
                    proxy = QNetworkProxy(kind, url.host(), port)
        self.manager.setProxy(proxy)

    @Slot(str, str)
    def check(self, folder, file):
        if self.error:
            self.finished.emit(self.Result.FAILED, self.error)
            return
        reply = self.manager.get(self.request(f"{self.SERVER}/{folder}/LAST_CHANGE"))

        def finished():
            reply.deleteLater()
            text = bytes(reply.readAll()).decode("ascii", "replace").strip()
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.finished.emit(self.Result.FAILED, f"Check failed: {reply.errorString()}")
            elif not text.isdigit() or int(text) <= 0:
                self.finished.emit(self.Result.FAILED, "Check failed: unexpected revision reply")
            else:
                self.checked.emit(folder, file, int(text))
        reply.finished.connect(finished)

    @Slot(str, str, int)
    def download(self, url, path, segments):
        """Fetches url into path in parallel ranges, through path.part."""
        if self.error:
            self.finished.emit(self.Result.FAILED, self.error)
            return
        part = QFile(path + ".part")
        self.cancelled = False
        size, md5, step, received = 0, "", 4 << 20, []
        ranges = []
        offset = 0
        stopped = False
        ranged, left = False, 0
        clock = QElapsedTimer()
        clock.start()

        def fail(message):
            nonlocal stopped
            if stopped:
                return
            stopped = True
            # Aborting a reply finishes it with an error; it is no longer listed by then
            replies, self.replies = self.replies, []
            for reply in replies:
                reply.abort()
            part.close()
            part.remove()
            if self.cancelled:
                self.finished.emit(self.Result.CANCELLED, "")
            else:
                self.finished.emit(self.Result.FAILED, f"Download failed: {message}")

        def intact(name):
            """Whether name has the server's size and MD5."""
            if QFileInfo(name).size() != size:
                return False
            digest = hashlib.md5()
            try:
                with open(name, "rb") as file:
                    while chunk := file.read(1 << 20):
                        digest.update(chunk)
            except OSError:
                return False
            return base64.b64encode(digest.digest()).decode() == md5

        def write(reply, index):
            if reply in self.replies:
                start, end = ranges[index]
                status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
                if status != (206 if ranged else 200):
                    return
                if ranged and bytes(reply.rawHeader(b"Content-Range")) != (
                        f"bytes {reply.property('start')}-{end - 1}/{size}".encode()):
                    fail("unexpected Content-Range")
                    return
                data = reply.readAll()
                if len(data) > end - start - received[index]:
                    fail("the server sent more data than requested")
                    return
                if not part.seek(start + received[index]):
                    fail(part.errorString())
                    return
                # The file is full size from the start, so a short write would go unnoticed later
                if part.write(data) != len(data):
                    fail(part.errorString())
                    return
                received[index] += len(data)
                # Every chunk would flood the window with updates
                if clock.elapsed() > self.INTERVAL:
                    clock.restart()
                    self.progress.emit(sum(received), size)

        def fetch(index):
            """Requests what is left of segment index."""
            nonlocal offset, left
            if index == len(ranges):
                if offset >= size:
                    return
                end = min(size, offset + step) if ranged else size
                ranges.append((offset, end))
                received.append(0)
                offset = end
                left += 1
            start, end = ranges[index]
            request = self.request(url)
            if ranged:
                request.setRawHeader(
                    b"Range", f"bytes={start + received[index]}-{end - 1}".encode())
            reply = self.manager.get(request)
            reply.setProperty("start", start + received[index])
            timer = QElapsedTimer()
            timer.start()
            reply.readyRead.connect(lambda: write(reply, index))
            reply.finished.connect(lambda: finished(reply, index, timer.elapsed()))
            self.replies.append(reply)

        def retry(index):
            # The download may have failed or been cancelled meanwhile
            if stopped or not part.isOpen():
                return
            if self.cancelled:
                fail("")
            else:
                fetch(index)

        def finished(reply, index, elapsed):
            nonlocal left, step, stopped
            reply.deleteLater()
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            write(reply, index)
            if reply not in self.replies:
                return
            self.replies.remove(reply)
            start, end = ranges[index]
            complete = received[index] == end - start
            if self.cancelled:
                fail("")
            elif ranged and not complete and (reply.error() in self.RETRIABLE or (
                    reply.error() == QNetworkReply.NetworkError.NoError and status == 206)):
                QTimer.singleShot(self.DELAY, lambda: retry(index))
            elif reply.error() != QNetworkReply.NetworkError.NoError:
                fail(reply.errorString())
            elif status != (206 if ranged else 200):
                fail(f"unexpected HTTP status {status}")
            elif not complete:
                fail("the file is incomplete")
            else:
                left -= 1
                if ranged:
                    transferred = end - reply.property("start")
                    step = (1 if transferred * 1000 < (1 << 20) * max(elapsed, 1) else 4) << 20
                if offset < size:
                    fetch(len(ranges))
                if left:
                    return
                part.close()
                if not intact(part.fileName()):
                    fail("the file is incomplete or does not match the server's checksum")
                elif (QFile.exists(path) and not QFile.remove(path)) or not part.rename(path):
                    fail(part.errorString())
                else:
                    stopped = True
                    self.progress.emit(size, size)
                    self.finished.emit(self.Result.DONE, "")

        def describe():
            head = self.manager.head(self.request(url))
            self.replies = [head]
            head.finished.connect(lambda: described(head))

        def redescribe():
            if stopped:
                return
            if self.cancelled:
                fail("")
            else:
                describe()

        def described(head):
            nonlocal size, md5, ranged, stopped
            head.deleteLater()
            self.replies = []
            size = head.header(QNetworkRequest.KnownHeaders.ContentLengthHeader) or 0
            hashes = bytes(head.rawHeader(b"x-goog-hash")).decode("ascii", "replace")
            md5 = next((each.strip()[4:] for each in hashes.split(",") if each.strip().startswith("md5=")), "")
            ranged = bytes(head.rawHeader(b"accept-ranges")) == b"bytes"
            count = max(1, min(segments, 8)) if ranged else 1
            if not self.cancelled and head.error() in self.RETRIABLE:
                QTimer.singleShot(self.DELAY, redescribe)
            elif head.error() != QNetworkReply.NetworkError.NoError:
                fail(head.errorString())
            elif size <= 0:
                fail("the server did not report a size")
            elif not md5:
                fail("the server did not report a checksum")
            elif intact(path):
                stopped = True
                self.progress.emit(size, size)
                self.finished.emit(self.Result.DONE, "")
            elif not QDir().mkpath(path.rsplit("/", 1)[0]) or not part.open(
                    QIODevice.OpenModeFlag.ReadWrite | QIODevice.OpenModeFlag.Truncate) or not part.resize(size):
                fail(part.errorString())
            else:
                for _ in range(count):
                    fetch(len(ranges))
        describe()

    @Slot()
    def cancel(self):
        # The first reply's error takes the rest down with it; a segment
        # waiting to retry sees the flag when its pause ends
        self.cancelled = True
        if self.replies:
            self.replies[0].abort()


class Window(QWidget):
    proxied = Signal(str, str, int)
    requested = Signal(str, str)
    ordered = Signal(str, str, int)
    aborted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chromium Updater")
        self.setFixedWidth(520)
        self.platform = Platform()
        self.settings = Settings(self.platform)
        self.remote = None
        self.url = ""
        self.revision = 0
        self.active = False

        self.status = QLabel("Ready")
        self.targetBadge = QLabel()
        self.targetBadge.setToolTip("Selected download target")
        self.targetBadge.setStyleSheet(
            "border: 1px solid palette(mid); border-radius: 3px; padding: 2px 6px;")
        self.targetBadge.setFixedHeight(self.targetBadge.sizeHint().height())

        status = QHBoxLayout()
        status.addWidget(self.status, 1)
        status.addWidget(self.targetBadge, 0, Qt.AlignRight)

        self.checkButton = QPushButton("Check for Updates")
        self.actionButton = QPushButton("Download")
        self.actionButton.clicked.connect(self.act)
        self.urlButton = QPushButton("Copy URL")
        self.urlButton.clicked.connect(lambda: QApplication.clipboard().setText(self.url))
        settingsButton = QPushButton("Settings")
        settingsButton.setCheckable(True)

        self.progress = QProgressBar()

        progress = QHBoxLayout()
        progress.addWidget(self.progress)
        progress.addWidget(self.urlButton)

        actions = QHBoxLayout()
        actions.addWidget(self.checkButton)
        actions.addWidget(self.actionButton)
        actions.addStretch()
        actions.addWidget(settingsButton)

        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SetNoConstraint)
        layout.addLayout(status)
        layout.addLayout(progress)
        layout.addLayout(actions)
        self.settingsPanel = self.panel(self.checkButton)
        self.settingsPanel.hide()
        layout.addWidget(self.settingsPanel)
        settingsButton.toggled.connect(self.settingsPanel.setVisible)
        self.apply()

        self.downloader = Downloader()
        self.worker = QThread(self)
        self.downloader.moveToThread(self.worker)
        self.proxied.connect(self.downloader.proxy)
        self.requested.connect(self.downloader.check)
        self.ordered.connect(self.downloader.download)
        self.aborted.connect(self.downloader.cancel)
        self.downloader.checked.connect(self.checked)
        self.downloader.progress.connect(self.progressed)
        self.downloader.finished.connect(self.finished)
        self.worker.start()
        self.checkButton.clicked.connect(self.check)
        if self.settings.get(Settings.Option.CHECK):
            QTimer.singleShot(0, self.check)

    def panel(self, checkButton):
        target = self.settings.get(Settings.Option.PLATFORM)
        platforms = [("Auto", "")] + [(label, value) for value, label in Platform.supported().items()]
        if target and target not in Platform.supported():
            platforms.append((f"Unknown target: {target}", target))
        self.platformCombo = self.choice(Settings.Option.PLATFORM, platforms)

        self.packageCombo = self.choice(Settings.Option.WINPACKAGE, [
            ("EXE installer", Platform.Package.EXE.name),
            ("ZIP archive", Platform.Package.ZIP.name),
        ])

        autoCheck = QCheckBox("Check for updates on startup")
        autoCheck.setToolTip("Check the selected target when the updater opens; no automatic download or installation")
        self.extractCheck = QCheckBox("Extract downloaded archive")
        self.installCheck = QCheckBox("Install extracted Chromium")
        self.prepareCheck = QCheckBox("Prepare Chromium for macOS")
        self.prepareCheck.setToolTip(
            "Remove quarantine; repair only the outer app signature for the known resource error.")
        self.systemCheck = QCheckBox("Install system-wide for Windows")
        self.systemCheck.setToolTip("Windows EXE installer: --system-level; requires administrator approval")
        self.desktopCheck = QCheckBox("Create desktop shortcut for Windows")
        self.desktopCheck.setToolTip("Windows ZIP extraction only")
        for checkbox, option in (
                (autoCheck, Settings.Option.CHECK),
                (self.extractCheck, Settings.Option.EXTRACT),
                (self.installCheck, Settings.Option.INSTALL),
                (self.prepareCheck, Settings.Option.MACSIGN),
                (self.systemCheck,  Settings.Option.WINSYSTEM),
                (self.desktopCheck, Settings.Option.WINDESKTOP)):
            checkbox.setChecked(self.settings.get(option))
            checkbox.toggled.connect(lambda checked, key=option: (
                self.settings.set(key, checked),
                self.apply(),
            ))

        refreshButton = QPushButton("Manual refresh")
        refreshButton.setToolTip("Check for updates now")
        refreshButton.clicked.connect(checkButton.clicked)
        autoCheck.toggled.connect(refreshButton.setVisible)
        autoCheck.toggled.connect(lambda enabled: checkButton.setVisible(not enabled))

        self.proxyMode = self.choice(Settings.Option.PROXYMODE,
                                     [(mode.name.title(), mode.name) for mode in Proxy.Mode])
        self.proxyHost = QLineEdit(self.settings.get(Settings.Option.PROXYHOST))
        self.proxyHost.setMinimumWidth(240)
        self.proxyHost.setPlaceholderText("proxy.example.com")
        self.proxyHost.setToolTip("Hostname only; authenticated proxies are not supported yet")
        self.proxyHost.editingFinished.connect(
            lambda: self.settings.set(Settings.Option.PROXYHOST, self.proxyHost.text().strip()))
        self.proxyPort = QSpinBox()
        segments = QSpinBox()
        segments.setToolTip("Parallel connections for one download")
        cleanup = QSpinBox()
        cleanup.setToolTip("Recent revisions kept per platform; 0 removes the download after successful extraction")
        for spin, option, low, high in (
                (self.proxyPort, Settings.Option.PROXYPORT, 1, 65535),
                (segments, Settings.Option.SEGMENTS, 1, 8),
                (cleanup, Settings.Option.CLEANUP, 0, 9)):
            spin.setRange(low, high)
            spin.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            spin.setValue(self.settings.get(option))
            spin.valueChanged.connect(lambda value, key=option: self.settings.set(key, value))
        # A one-digit box is too narrow; match the port's width
        for spin in (segments, cleanup):
            spin.setFixedWidth(self.proxyPort.sizeHint().width())

        # Qt 5 forms keep the spacing of hidden rows, so whatever hides is a
        # section of its own; only this system's options are added at all
        self.labels = {}
        system = self.platform.system()
        general = self.box("General", self.section(
            ("Platform", self.platformCombo),
            ("Download folder", self.directory(Settings.Option.DOWNLOADDIR)),
            self.row(autoCheck, refreshButton),
            ("Connections", segments),
            ("Keep downloads", cleanup)))
        self.installDirectory = self.directory(Settings.Option.INSTALLDIR)
        if system == "windows":
            self.exeSection = self.section(self.row(self.installCheck), self.row(self.systemCheck))
            self.zipSection = self.section(self.row(self.extractCheck),
                                           ("Install folder", self.installDirectory),
                                           self.row(self.desktopCheck))
            sections = (self.section(("Package", self.packageCombo)), self.exeSection, self.zipSection)
        else:
            rows = [self.row(self.extractCheck), self.row(self.installCheck),
                    ("Install folder", self.installDirectory)]
            if system == "macos":
                rows.append(self.row(self.prepareCheck))
            sections = (self.section(*rows),)
        self.platformBox = self.box(
            {"macos": "macOS", "windows": "Windows", "linux": "Linux"}.get(system, ""), *sections)
        self.manualSection = self.section(("Host", self.proxyHost), ("Port", self.proxyPort))
        proxy = self.box("Proxy", self.section(("Mode", self.proxyMode)), self.manualSection)

        width = max(label.sizeHint().width() for label in self.labels.values())
        for label in self.labels.values():
            label.setFixedWidth(width)
        refreshButton.setVisible(autoCheck.isChecked())
        checkButton.setVisible(not autoCheck.isChecked())

        settingsPanel = QWidget()
        layout = QVBoxLayout(settingsPanel)
        layout.setContentsMargins(0, 0, 0, 0)
        for box in (general, self.platformBox, proxy):
            layout.addWidget(box)
        return settingsPanel

    @staticmethod
    def box(title, *sections):
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        for section in sections:
            layout.addWidget(section)
        return box

    def section(self, *rows):
        """A form of (label, field) pairs and full-width rows."""
        widget = QWidget()
        layout = QFormLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        layout.setLabelAlignment(Qt.AlignLeft)
        layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        for row in rows:
            if isinstance(row, tuple):
                text, field = row
                self.labels[field] = QLabel(text)
                layout.addRow(self.labels[field], field)
            else:
                layout.addRow(row)
        return widget

    def choice(self, option, items):
        combo = QComboBox()
        combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        for label, value in items:
            combo.addItem(label, value)
        index = combo.findData(self.settings.get(option))
        combo.setCurrentIndex(index if index >= 0 else combo.findData(self.settings.defaults[option]))
        combo.currentIndexChanged.connect(lambda: (
            self.settings.set(option, combo.currentData()),
            self.apply(),
        ))
        return combo

    def event(self, event):
        if event.type() == QEvent.LayoutRequest:
            self.setFixedHeight(self.sizeHint().height())
        return super().event(event)

    @staticmethod
    def row(checkbox, button=None):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(checkbox)
        layout.addStretch()
        if button is not None:
            layout.addWidget(button)
        return widget

    def directory(self, option):
        widget = QWidget()
        edit = QLineEdit(self.settings.get(option))
        edit.setReadOnly(True)
        edit.setMinimumWidth(240)
        edit.textChanged.connect(lambda value: self.settings.set(option, value))
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda: edit.setText(directory) if (
            directory := QFileDialog.getExistingDirectory(self, "Choose folder", edit.text())
        ) else None)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit)
        layout.addWidget(browse)
        return widget

    def apply(self):
        target = self.platformCombo.currentData() or self.platform.current()
        self.targetBadge.setText(Platform.supported().get(target, "Unknown target"))
        native = self.platform.native(target)
        system = self.platform.system()
        windows = native and system == "windows"
        macos = native and system == "macos"
        exe = windows and self.packageCombo.currentData() == Platform.Package.EXE.name
        portable = windows and not exe
        extract = native and not exe and self.extractCheck.isChecked()
        install = (exe or (extract and not portable)) and self.installCheck.isChecked()
        manual = self.proxyMode.currentData() == Proxy.Mode.MANUAL.name
        self.platformBox.setVisible(native)
        if system == "windows":
            self.exeSection.setVisible(exe)
            self.zipSection.setVisible(not exe)
        for widget, enabled in (
                (self.extractCheck, not exe),
                (self.installCheck, exe or (extract and not portable)),
                (self.installDirectory, (install and not exe) or (portable and extract)),
                (self.prepareCheck, extract and macos),
                (self.systemCheck, install and exe),
                (self.desktopCheck, portable and extract)):
            widget.setEnabled(enabled)
            if widget in self.labels:
                self.labels[widget].setEnabled(enabled)
        self.manualSection.setVisible(manual)
        self.installCheck.setText("Run Chromium installer" if exe else "Install extracted Chromium")

        if install:
            action = "Download and Install"
        elif extract:
            action = "Download and Extract"
        else:
            action = "Download"
        self.actionButton.setText("Cancel" if self.active else action)

        # A checked revision belongs to one server file
        remote = Platform.remote(target, self.packageCombo.currentData())
        if remote != self.remote:
            self.remote = remote
            self.url = ""
            self.urlButton.setEnabled(False)
            self.actionButton.setEnabled(False)
            self.progress.setRange(0, 100)
            self.status.setText("Ready")

    def work(self, active):
        self.active = active
        self.settingsPanel.setEnabled(not active)
        self.checkButton.setEnabled(not active)
        self.apply()

    def check(self):
        folder, file = self.remote
        if not folder:
            self.status.setText("No snapshot for this target")
            return
        self.status.setText("Checking…")
        self.progress.setRange(0, 0)
        self.proxied.emit(self.proxyMode.currentData(), self.proxyHost.text().strip(), self.proxyPort.value())
        self.requested.emit(folder, file)

    def checked(self, folder, file, revision):
        if (folder, file) != self.remote:
            return
        self.revision = revision
        self.url = f"{Downloader.SERVER}/{folder}/{revision}/{file}"
        self.urlButton.setEnabled(True)
        self.actionButton.setEnabled(True)
        self.progress.setRange(0, 100)
        self.status.setText(f"Revision {revision} is available")

    def act(self):
        if self.active:
            self.aborted.emit()
            return
        folder, file = self.remote
        self.work(True)
        self.status.setText("Downloading…")
        self.progress.setRange(0, 0)
        self.proxied.emit(self.proxyMode.currentData(), self.proxyHost.text().strip(), self.proxyPort.value())
        self.ordered.emit(
            self.url, f"{self.settings.get(Settings.Option.DOWNLOADDIR)}/{folder}/{self.revision}/{file}",
            self.settings.get(Settings.Option.SEGMENTS))
        self.clock = QElapsedTimer()
        self.clock.start()

    def progressed(self, received, total):
        self.progress.setRange(0, total)
        self.progress.setValue(received)
        # Average since the download started
        speed = received * 1000 // max(self.clock.elapsed(), 1)
        rate = f"{speed / (1 << 20):.1f} MB/s" if speed >= 1 << 20 else f"{speed >> 10} KB/s"
        self.status.setText(f"Downloading… {received >> 20} of {total >> 20} MB · {rate}")

    def finished(self, result, message):
        self.work(False)
        if result == Downloader.Result.DONE:
            self.status.setText(f"Downloaded revision {self.revision}")
            return
        self.progress.setRange(0, 100)
        self.progress.reset()
        self.status.setText("Cancelled" if result == Downloader.Result.CANCELLED else message)

    def closeEvent(self, event):
        self.worker.quit()
        self.worker.wait()
        super().closeEvent(event)


if __name__ == "__main__":
    if sys.platform == "win32":
        # The taskbar button takes its icon from the process's application
        # id, not the window; run through python.exe it would be Python's.
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ChromiumUpdater.ChromiumUpdaterPy")

    app = QApplication(sys.argv)
    app.setApplicationName("ChromiumUpdaterPy")
    app.setOrganizationName("ChromiumUpdater")
    app.setWindowIcon(QIcon(resource("icon.svg")))
    window = Window()
    window.show()
    sys.exit(app.exec())
