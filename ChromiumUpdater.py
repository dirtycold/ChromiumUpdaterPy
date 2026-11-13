"""Chromium Updater: a compact Qt window, built one step at a time."""

import base64
import hashlib
import os
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
from enum           import  Enum
from pathlib        import  Path

from qtpy.QtCore    import (QDir, QElapsedTimer, QEvent, QFile, QFileInfo, 
                            QIODevice, QLockFile, QObject, QSaveFile, QSettings, QStandardPaths, QStorageInfo,
                            QSysInfo, QThread, QTimer, QUrl, Qt, Signal, Slot,
                            )
from qtpy.QtGui     import  QDesktopServices, QIcon
from qtpy.QtNetwork import (QNetworkAccessManager, QNetworkProxy, 
                            QNetworkProxyFactory, QNetworkProxyQuery, QNetworkReply, QNetworkRequest,
                            )
from qtpy.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, 
                            QFormLayout, QGroupBox, QHBoxLayout, QLabel, 
                            QLayout, QLineEdit, QMainWindow, QProgressBar, QPushButton,
                            QSizePolicy, QSpinBox, QStyle, QToolBar, QToolButton, QVBoxLayout, QWidget,
                            )


def resource(name):
    """Bundled file path: PyInstaller's unpack folder when frozen, else next to this script."""
    return str(Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name)


class Platform:
    class File:
        MAC         = "chrome-mac.zip"
        WINDOWS     = "chrome-win.zip"
        LINUX       = "chrome-linux.zip"
        INSTALLER   = "mini_installer.exe"

    class Directory(Enum):
        DOWNLOAD = 0
        INSTALL  = 1

    class Package(Enum):
        EXE = 0
        ZIP = 1

    def __init__(self):
        self.os = {"darwin": "macos", "win32": "windows", "linux": "linux"}.get(sys.platform, "")
        self.arch = {
            "arm64":    "arm64",
            "x86_64":   "x64",
            "i386":     "x86",
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
            "macos":    "/Applications",
            "windows":  QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation) + "/Chromium",
            "linux":    QStandardPaths.writableLocation(QStandardPaths.HomeLocation) + "/.local/opt/chromium",
        }.get(self.os, "")

    def payload(self):
        # Snapshot archive layouts and executable names:
        # https://chromium.googlesource.com/chromium/src/+/131.0.6768.0/tools/bisect-builds.py
        return {
            "macos":    ("chrome-mac/Chromium.app", "Contents/MacOS/Chromium"),
            "windows":  ("chrome-win", "chrome.exe"),
            "linux":    ("chrome-linux", "chrome"),
        }.get(self.os, ("", ""))

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
            return folder, Platform.File.INSTALLER if package == Platform.Package.EXE.name else Platform.File.WINDOWS
        return folder, Platform.File.MAC if target.startswith("macos") else Platform.File.LINUX


class Proxy:
    class Mode(Enum):
        DIRECT = 0
        SYSTEM = 1
        MANUAL = 2


class Settings:
    class Option(Enum):
        PLATFORM        = "Platform"
        CHECK           = "Check"
        INSTALL         = "Install"
        CLEANUP         = "Cleanup"
        DOWNLOADDIR     = "Directory/Download"
        INSTALLDIR      = "Directory/Install"
        MACSIGN         = "Mac/Sign"
        WINPACKAGE      = "Win/Package"
        WINSYSTEM       = "Win/System"
        WINDESKTOP      = "Win/Desktop"
        LINUXDESKTOP    = "Linux/Desktop"
        PROXYMODE       = "Proxy/Mode"
        PROXYHOST       = "Proxy/Host"
        PROXYPORT       = "Proxy/Port"
        SEGMENTS        = "Segments"

    def __init__(self, platform: Platform):
        self.setting = QSettings()
        self.defaults = {
            self.Option.PLATFORM:       "",
            self.Option.CHECK:          False,
            self.Option.SEGMENTS:       4,
            self.Option.INSTALL:        platform.system() in {"macos", "windows"},
            self.Option.CLEANUP:        2,
            self.Option.DOWNLOADDIR:    platform.directory(Platform.Directory.DOWNLOAD),
            self.Option.INSTALLDIR:     platform.directory(Platform.Directory.INSTALL),
            self.Option.MACSIGN:        True,
            self.Option.WINPACKAGE:     Platform.Package.EXE.name,
            self.Option.WINSYSTEM:      False,
            self.Option.WINDESKTOP:     False,
            self.Option.LINUXDESKTOP:   True,
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


class Updater(QObject):
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

    class Stage(Enum):
        IDLE        = 0
        CHECKING    = 1
        DOWNLOADING = 2
        EXTRACTING  = 3
        INSTALLING  = 4

    # A pulse: the report has changed; read it for the details
    changed = Signal()
    resolved = Signal()
    # A Result alone; the report has the details
    finished = Signal(object)

    def __init__(self):
        super().__init__()
        QNetworkProxyFactory.setUseSystemConfiguration(True)
        self.manager = QNetworkAccessManager(self)
        self.replies = []
        self.cancelled = False
        self.error = ""
        self.platform = Platform()
        self.log = []
        # One snapshot, always rebuilt whole and rebound, so any thread reads it consistently
        self.report = self.snapshot(self.Stage.IDLE, "", "", 0)

    def request(self, url):
        request = QNetworkRequest(QUrl(url))
        request.setTransferTimeout(self.TIMEOUT)
        request.setAttribute(QNetworkRequest.Attribute.Http2AllowedAttribute, False)
        request.setRawHeader(b"Accept-Encoding", b"identity")
        return request

    @Slot(dict)
    def proxy(self, proxy):
        mode, host, port = proxy["mode"], proxy["host"], proxy["port"]
        host = host.strip()
        self.error = ""
        try:
            proxy = {
                Proxy.Mode.DIRECT.name: QNetworkProxy(QNetworkProxy.ProxyType.NoProxy),
                Proxy.Mode.MANUAL.name: QNetworkProxy(QNetworkProxy.ProxyType.HttpProxy, host, port),
            }.get(mode, QNetworkProxy(QNetworkProxy.ProxyType.DefaultProxy))
            if mode == Proxy.Mode.MANUAL.name:
                if not host:
                    self.error = "Manual proxy needs a host"
                    return
                url = QUrl()
                url.setScheme("http")
                url.setHost(host)
                if not url.isValid() or not url.host() or any(character.isspace() for character in host) or not 1 <= port <= 65535:
                    self.error = "Invalid manual proxy; enter a hostname and port"
                    return
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
                else:
                    proxies = QNetworkProxyFactory.systemProxyForQuery(QNetworkProxyQuery(QUrl(self.SERVER)))
                    proxy = next((candidate for candidate in proxies if candidate.type() in (
                        QNetworkProxy.ProxyType.HttpProxy, QNetworkProxy.ProxyType.Socks5Proxy,
                        QNetworkProxy.ProxyType.NoProxy)), None)
                    if proxy is None:
                        if proxies:
                            self.error = "No supported system proxy was found"
                            return
                        proxy = QNetworkProxy(QNetworkProxy.ProxyType.NoProxy)
                    if proxy.type() != QNetworkProxy.ProxyType.NoProxy and not proxy.hostName().strip():
                        self.error = "System proxy has no host"
                        return
            self.manager.setProxy(proxy)
        finally:
            text = self.error or "Direct connection"
            if not self.error and proxy.type() != QNetworkProxy.ProxyType.NoProxy:
                url = QUrl()
                url.setScheme("socks5" if proxy.type() == QNetworkProxy.ProxyType.Socks5Proxy else "http")
                url.setHost(proxy.hostName())
                url.setPort(proxy.port())
                text = url.toString()
            self.route = {"mode": mode, "text": text, "valid": not bool(self.error)}
            self.resolved.emit()

    @staticmethod
    def snapshot(stage, folder, file, revision):
        return {"stage": stage, "folder": folder, "file": file, "revision": revision,
                "received": 0, "total": 0, "speed": 0, "error": "", "cancellable": True}

    def publish(self, **changes):
        self.report = {**self.report, **changes}
        self.changed.emit()

    @Slot(str, str, dict)
    def check(self, folder, file, proxy):
        self.proxy(proxy)
        if self.error:
            self.report = {**self.report, "folder": folder, "file": file, "error": self.error}
            self.finished.emit(self.Result.FAILED)
            return
        self.report = self.snapshot(self.Stage.CHECKING, folder, file, 0)
        self.changed.emit()
        reply = self.manager.get(self.request(f"{self.SERVER}/{folder}/LAST_CHANGE"))

        def finished():
            reply.deleteLater()
            text = bytes(reply.readAll()).decode("ascii", "replace").strip()
            # A reply that outlived its target or operation changes nothing
            report = self.report
            if (report["stage"], report["folder"], report["file"]) != (self.Stage.CHECKING, folder, file):
                return
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.report = {**report, "error": f"Check failed: {reply.errorString()}"}
                self.finished.emit(self.Result.FAILED)
            elif not text.isdigit() or int(text) <= 0:
                self.report = {**report, "error": "Check failed: unexpected revision reply"}
                self.finished.emit(self.Result.FAILED)
            else:
                self.report = {**report, "revision": int(text)}
                self.finished.emit(self.Result.DONE)
        reply.finished.connect(finished)

    @Slot(dict, dict)
    def download(self, proxy, options):
        """Fetches the checked revision's file in parallel ranges, through a .part file."""
        directory, segments, install = options[Settings.Option.DOWNLOADDIR], options[Settings.Option.SEGMENTS], options[Settings.Option.INSTALL]
        self.proxy(proxy)
        folder, file, revision = self.report["folder"], self.report["file"], self.report["revision"]
        if install and not self.error:
            native, _ = Platform.remote(self.platform.current(), Platform.Package.EXE.name)
            if folder != native or not native:
                self.error = "Installation requires the native target"
            elif self.platform.system() == "windows" and options[Settings.Option.WINDESKTOP] and file != Platform.File.INSTALLER:
                self.error = "Desktop shortcut creation is not implemented yet"
        if self.error or not revision:
            self.report = {**self.report, "error": self.error or "Check for updates first"}
            self.finished.emit(self.Result.FAILED)
            return
        self.report = self.snapshot(self.Stage.DOWNLOADING, folder, file, revision)
        self.changed.emit()
        url = f"{self.SERVER}/{folder}/{revision}/{file}"
        path = f"{directory}/{folder}/{revision}/{file}"
        part = QFile(path + ".part")
        self.cancelled = False
        size, md5, step, received = 0, "", 4 << 20, []
        ranges = []
        offset = 0
        stopped = False
        ranged, left = False, 0
        # One clock serves the progress throttle and the average speed
        clock = QElapsedTimer()
        clock.start()
        last = 0

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
            if not self.cancelled:
                self.report = {**self.report, "error": f"Download failed: {message}"}
            self.finished.emit(self.Result.CANCELLED if self.cancelled else self.Result.FAILED)

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
            nonlocal last
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
                if clock.elapsed() - last > self.INTERVAL:
                    last = clock.elapsed()
                    done = sum(received)
                    # The speed is the average since the download started
                    self.publish(received=done, total=size, speed=done * 1000 // max(last, 1))

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
            # Drain what a cleanly closed reply still buffers; an aborted one is not readable
            if reply.error() == QNetworkReply.NetworkError.NoError:
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
                    self.publish(received=size, total=size)
                    self.finished.emit(self.install(path, options) if install else self.Result.DONE)

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
                self.publish(received=size, total=size)
                self.finished.emit(self.install(path, options) if install else self.Result.DONE)
            elif not QDir().mkpath(path.rsplit("/", 1)[0]) or not part.open(
                    QIODevice.OpenModeFlag.ReadWrite | QIODevice.OpenModeFlag.Truncate) or not part.resize(size):
                fail(part.errorString())
            else:
                for _ in range(count):
                    fetch(len(ranges))
        describe()

    def extract(self, archive, destination):
        """Unpacks into the caller's staging directory."""
        self.publish(stage=self.Stage.EXTRACTING, received=0, total=0, speed=0)
        clock = QElapsedTimer()
        clock.start()
        try:
            base = Path(destination).resolve()
            with zipfile.ZipFile(archive) as source:
                entries = source.infolist()
                total = sum(entry.file_size for entry in entries)
                done = 0
                directories = []
                for entry in entries:
                    name = Path(entry.filename)
                    if name.is_absolute() or ".." in name.parts or not name.parts:
                        self.error = f"Unsafe path in the archive: {entry.filename}"
                        return False
                for entry in entries:
                    if self.cancelled:
                        return False
                    target = base / entry.filename
                    # Nothing may land outside the destination, through dots or a planted link
                    if not target.resolve().is_relative_to(base):
                        self.error = f"Unsafe path in the archive: {entry.filename}"
                        return False
                    if entry.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        directories.append((target, entry.external_attr >> 16))
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    mode = entry.external_attr >> 16
                    if stat.S_ISLNK(mode):
                        where = source.read(entry).decode("utf-8")
                        if Path(where).is_absolute() or not (
                                target.parent / where).resolve().is_relative_to(base):
                            self.error = f"Unsafe link in the archive: {entry.filename}"
                            return False
                        target.unlink(missing_ok=True)
                        os.symlink(where, target)
                        done += entry.file_size
                        continue
                    with source.open(entry) as data, open(target, "wb") as file:
                        while chunk := data.read(1 << 20):
                            if self.cancelled:
                                break
                            file.write(chunk)
                            done += len(chunk)
                            if clock.elapsed() > self.INTERVAL:
                                clock.restart()
                                self.publish(received=done, total=total)
                    if self.cancelled:
                        return False
                    if mode:
                        os.chmod(target, mode & 0o777)
                for directory, mode in sorted(directories, key=lambda item: len(item[0].parts), reverse=True):
                    if mode:
                        os.chmod(directory, mode & 0o777)
                if self.cancelled:
                    return False
            self.publish(received=total, total=total)
            return True
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as error:
            self.error = f"Extraction failed: {error}"
            return False

    def command(self, arguments, accepted=(0,), timeout=60):
        try:
            result = subprocess.run(arguments, capture_output=True, text=True,
                                    errors="replace", timeout=timeout)
            output = result.stdout + result.stderr
            self.log.append((arguments, output))
            if result.returncode in accepted:
                return result
            self.error = f"{Path(arguments[0]).name} failed: {output.strip()}"
        except (OSError, subprocess.SubprocessError) as error:
            self.error = str(error)
        return None

    def idle(self, destination):
        if self.platform.system() == "windows":
            result = self.command(["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/FO", "CSV", "/NH"])
            running = result is not None and '"chrome.exe"' in result.stdout.lower()
        else:
            _, executable = self.platform.payload()
            result = self.command(["pgrep", "-f", re.escape(str(destination / executable))], (0, 1))
            running = result is not None and result.returncode == 0
        if running:
            self.error = "Close Chromium before installing"
        return result is not None and not running

    def verify(self, application, prepare=False):
        _, executable = self.platform.payload()
        binary = application / executable
        try:
            if application.is_symlink() or not application.is_dir() or not binary.is_file():
                self.error = "The archive does not contain the expected Chromium application"
                return False
            if self.platform.system() == "macos":
                with open(application / "Contents/Info.plist", "rb") as file:
                    info = plistlib.load(file)
                if not isinstance(info, dict) or info.get("CFBundleIdentifier") != "org.chromium.Chromium":
                    self.error = "Unexpected application bundle identifier"
                    return False
                if not prepare:
                    return True
                for item in [application, *application.rglob("*")]:
                    if self.cancelled:
                        return False
                    attributes = self.command(["/usr/bin/xattr", "-s", str(item)])
                    if attributes is None:
                        return False
                    if "com.apple.quarantine" in attributes.stdout.splitlines():
                        if self.command(["/usr/bin/xattr", "-d", "-s", "com.apple.quarantine", str(item)]) is None:
                            return False
                arguments = ["/usr/bin/codesign", "--verify", "--strict", "--verbose=2", str(application)]
                if self.command(arguments) is None:
                    diagnostic = f"{application}: code has no resources but signature indicates they must be present"
                    if not prepare or not self.log or self.log[-1][1].strip() != diagnostic:
                        return False
                    if self.command(["/usr/bin/codesign", "--force", "--sign", "-", str(application)]) is None:
                        return False
                    if self.command(arguments) is None:
                        return False
                    self.error = ""
            return True
        except (OSError, ValueError, plistlib.InvalidFileException) as error:
            self.error = str(error)
            return False

    def install(self, archive, options):
        """Installs a verified download; staging is always disposable."""
        self.error = ""
        self.publish(stage=self.Stage.INSTALLING, received=0, total=0, speed=0)
        installer = Path(archive).name == Platform.File.INSTALLER
        directory = (QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
                     if installer else options[Settings.Option.INSTALLDIR])
        if not directory:
            self.report = {**self.report, "error": "Choose an install folder"}
            return self.Result.FAILED
        lock = None
        previous, target = None, None
        moved, replaced, success = False, False, False
        try:
            destination = Path(directory).resolve()
            destination.mkdir(parents=True, exist_ok=True)
            lock = QLockFile(str(destination / ".chromium-updater.lock"))
            lock.setStaleLockTime(0)
            if not lock.tryLock(0):
                self.error = "Another updater is using this install folder"
                return self.Result.FAILED
            relative, _ = self.platform.payload()
            target = destination / Path(relative).name
            if not self.idle(target):
                return self.Result.FAILED
            if self.cancelled:
                return self.Result.CANCELLED
            if installer:
                self.publish(stage=self.Stage.INSTALLING, cancellable=False)
                arguments = [str(Path(archive).resolve())]
                if options[Settings.Option.WINSYSTEM]:
                    arguments.append("--system-level")
                return self.Result.DONE if self.command(arguments, timeout=None) is not None else self.Result.FAILED
            previous = target.with_name(target.name + ".previous")
            marker = destination / ("." + target.name + ".revision")
            if target.is_symlink() or previous.is_symlink():
                self.error = "The installation or backup is a symlink; choose another folder"
                return self.Result.FAILED
            if not target.exists() and previous.exists():
                previous.rename(target)
            if marker.exists() and int(marker.read_text().strip()) > self.report["revision"]:
                self.error = "A newer Chromium revision is already installed"
                return self.Result.FAILED
            with zipfile.ZipFile(archive) as source:
                required = sum(entry.file_size for entry in source.infolist())
            if QStorageInfo(str(destination)).bytesAvailable() < required:
                self.error = "Not enough free space to extract Chromium"
                return self.Result.FAILED
            with tempfile.TemporaryDirectory(prefix=".chromium-", dir=destination) as staging:
                if not self.extract(archive, staging):
                    return self.Result.CANCELLED if self.cancelled else self.Result.FAILED
                application = Path(staging) / relative
                if not self.verify(application, options[Settings.Option.MACSIGN]):
                    return self.Result.CANCELLED if self.cancelled else self.Result.FAILED
                if not self.idle(target):
                    return self.Result.FAILED
                if self.cancelled:
                    return self.Result.CANCELLED
                self.publish(stage=self.Stage.INSTALLING, received=0, total=0, cancellable=False)
                if previous.exists() and not QDir(str(previous)).removeRecursively():
                    self.error = "Could not remove the older backup"
                    return self.Result.FAILED
                if target.exists():
                    target.rename(previous)
                    moved = True
                application.rename(target)
                replaced = True
                if not self.verify(target):
                    return self.Result.FAILED
                record = QSaveFile(str(marker))
                revision = str(self.report["revision"]).encode()
                if not record.open(QIODevice.WriteOnly) or record.write(revision) != len(revision) or not record.commit():
                    self.error = "Could not record the installed revision"
                    return self.Result.FAILED
                success = True
            if self.platform.system() == "linux" and options[Settings.Option.LINUXDESKTOP]:
                directory = Path(QStandardPaths.writableLocation(QStandardPaths.ApplicationsLocation))
                directory.mkdir(parents=True, exist_ok=True)
                executable = str(target / "chrome")
                if any(character in executable for character in "\n\r="):
                    self.error = "Chromium installed, but the path cannot be used in a desktop launcher"
                    return self.Result.FAILED
                executable = executable.replace("\\", "\\\\\\\\").replace("%", "%%")
                for character in ('"', '`', '$'):
                    executable = executable.replace(character, "\\\\" + character)
                desktop = ("[Desktop Entry]\nType=Application\nName=Chromium (snapshot)\n"
                           f'Exec="{executable}" %U\n'
                           "Icon=chromium\nTerminal=false\nCategories=Network;WebBrowser;\n").encode()
                launcher = QSaveFile(str(directory / "chromium-updater.desktop"))
                if not launcher.open(QIODevice.WriteOnly) or launcher.write(desktop) != len(desktop) or not launcher.commit():
                    self.error = "Chromium installed, but the application launcher could not be saved"
                    return self.Result.FAILED
            return self.Result.DONE
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as error:
            self.error = f"{'Launcher creation failed; Chromium is installed' if success else 'Installation failed'}: {error}"
            return self.Result.FAILED
        finally:
            if not success and (moved or replaced):
                try:
                    if replaced and not QDir(str(target)).removeRecursively():
                        self.error += "; could not remove the failed installation"
                    if moved:
                        previous.rename(target)
                except OSError as error:
                    self.error += f"; restore {previous} manually: {error}"
            if lock is not None:
                lock.unlock()
            self.report = {**self.report, "error": self.error, "cancellable": True}

    @Slot()
    def cancel(self):
        # The first reply's error takes the rest down with it; a segment
        # waiting to retry sees the flag when its pause ends
        self.cancelled = True
        if self.replies:
            self.replies[0].abort()


class ToolbarControls(QWidget):
    def sizeHint(self):
        size = super().sizeHint()
        if self.layout() is not None:
            size.setHeight(self.layout().totalHeightForWidth(self.width()))
        return size

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.updateGeometry()


class Window(QMainWindow):
    # Seconds required to hold Reset settings
    RESET = 3

    queried = Signal(str, str, dict)
    requested = Signal(dict, dict)
    proxied = Signal(dict)
    aborted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chromium Updater")
        self.setFixedWidth(520)
        self.platform = Platform()
        self.settings = Settings(self.platform)
        self.remote = None
        self.url = ""
        self.active = False
        self.proxySelection = None

        self.status = QLabel("Ready")
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.status.setWordWrap(True)
        self.badge = QLabel()
        self.badge.setToolTip("Selected download target")
        self.badge.setStyleSheet(
            "border: 1px solid palette(mid); border-radius: 3px; padding: 2px 6px;")
        self.badge.setFixedHeight(self.badge.sizeHint().height())

        status = QHBoxLayout()
        status.addWidget(self.status, 1)
        status.addWidget(self.badge, 0, Qt.AlignRight)

        self.checkButton = QPushButton("Check for Updates")
        self.actionButton = QPushButton("Download")
        self.actionButton.clicked.connect(self.act)
        self.urlButton = QToolButton()
        self.urlButton.setIcon(QIcon.fromTheme("edit-copy", self.style().standardIcon(QStyle.SP_FileIcon)))
        self.urlButton.setToolTip("Copy download URL")
        self.urlButton.setAccessibleName("Copy download URL")
        self.urlButton.clicked.connect(lambda: QApplication.clipboard().setText(self.url))
        settingsButton = QPushButton("Advanced …")
        settingsButton.setCheckable(True)

        self.progress = QProgressBar()

        actions = QHBoxLayout()
        actions.addWidget(self.checkButton)
        actions.addWidget(self.actionButton)
        actions.addStretch()
        actions.addWidget(settingsButton)

        controls = ToolbarControls()
        controls.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(controls)
        layout.setSizeConstraint(QLayout.SetNoConstraint)
        layout.addLayout(status)
        layout.addWidget(self.progress)
        layout.addLayout(actions)
        toolbar = QToolBar("Update controls", self)
        toolbar.setObjectName("updateControls")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setAllowedAreas(Qt.TopToolBarArea)
        toolbar.addWidget(controls)
        self.addToolBar(Qt.TopToolBarArea, toolbar)
        if sys.platform == "darwin":
            self.setUnifiedTitleAndToolBarOnMac(True)

        self.settingsPanel = self.panel(self.checkButton)
        self.setCentralWidget(self.settingsPanel)
        self.settingsPanel.hide()
        settingsButton.toggled.connect(lambda visible: self.settingsPanel.setVisible(visible))
        self.apply()

        self.updater = Updater()
        self.worker = QThread(self)
        self.updater.moveToThread(self.worker)
        self.proxied.connect(self.updater.proxy)
        self.updater.resolved.connect(self.resolved)
        self.queried.connect(self.updater.check)
        self.requested.connect(self.updater.download)
        self.aborted.connect(self.updater.cancel)
        self.updater.changed.connect(self.changed)
        self.updater.finished.connect(self.finished)
        self.worker.start()
        self.apply()
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
        self.installCheck = QCheckBox("Install Chromium")
        self.prepareCheck = QCheckBox("Prepare Chromium for macOS")
        self.prepareCheck.setToolTip(
            "Remove quarantine; repair only the outer app signature for the known resource error.")
        self.systemCheck = QCheckBox("Install system-wide for Windows")
        self.systemCheck.setToolTip("Windows EXE installer: --system-level; requires administrator approval")
        self.desktopCheck = QCheckBox("Create desktop shortcut for Windows")
        self.desktopCheck.setToolTip("Windows ZIP installation only")
        desktopOption = Settings.Option.WINDESKTOP
        if self.platform.system() == "linux":
            desktopOption = Settings.Option.LINUXDESKTOP
            self.desktopCheck.setText("Create application launcher for Linux")
            self.desktopCheck.setToolTip("Add Chromium to this user's application menu")
        for checkbox, option in (
                (autoCheck, Settings.Option.CHECK),
                (self.installCheck, Settings.Option.INSTALL),
                (self.prepareCheck, Settings.Option.MACSIGN),
                (self.systemCheck,  Settings.Option.WINSYSTEM),
                (self.desktopCheck, desktopOption)):
            checkbox.setChecked(self.settings.get(option))
            checkbox.toggled.connect(lambda checked, key=option: (
                self.settings.set(key, checked),
                self.apply(),
            ))

        refreshButton = QToolButton()
        refreshButton.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        refreshButton.setToolTip("Check for updates now")
        refreshButton.setAccessibleName("Check for updates now")
        refreshButton.clicked.connect(checkButton.clicked)
        autoCheck.toggled.connect(refreshButton.setVisible)
        autoCheck.toggled.connect(lambda enabled: checkButton.setVisible(not enabled))
        checkActions = QWidget()
        checkLayout = QHBoxLayout(checkActions)
        checkLayout.setContentsMargins(0, 0, 0, 0)
        checkLayout.addWidget(refreshButton)
        checkLayout.addWidget(self.urlButton)

        self.proxyMode = self.choice(Settings.Option.PROXYMODE,
                                     [(mode.name.title(), mode.name) for mode in Proxy.Mode])
        self.proxyHost = QLineEdit(self.settings.get(Settings.Option.PROXYHOST))
        self.proxyHost.setMinimumWidth(240)
        self.proxyHost.setPlaceholderText("proxy.example.com")
        self.proxyHost.setToolTip("Hostname only; authenticated proxies are not supported yet")
        self.proxyHost.editingFinished.connect(
            lambda: (self.settings.set(Settings.Option.PROXYHOST, self.proxyHost.text().strip()), self.apply()))
        self.proxyPort = QSpinBox()
        segments = QSpinBox()
        segments.setToolTip("Parallel connections for one download")
        cleanup = QSpinBox()
        cleanup.setToolTip("Recent revisions kept per platform; 0 removes the download after successful installation")
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
        self.proxyPort.valueChanged.connect(lambda: self.apply())

        # Qt 5 forms keep the spacing of hidden rows, so whatever hides is a
        # section of its own; only this system's options are added at all
        self.labels = {}
        system = self.platform.system()
        general = self.box("General", self.section(
            ("Platform", self.platformCombo),
            ("Download folder", self.directory(Settings.Option.DOWNLOADDIR)),
            self.row(autoCheck, checkActions),
            ("Connections", segments),
            ("Keep downloads", cleanup)))
        self.installDirectory = self.directory(Settings.Option.INSTALLDIR)
        if system == "windows":
            self.exeSection = self.section(self.row(self.systemCheck))
            self.zipSection = self.section(("Install folder", self.installDirectory),
                                           self.row(self.desktopCheck))
            sections = (self.section(("Package", self.packageCombo), self.row(self.installCheck)),
                        self.exeSection, self.zipSection)
        else:
            rows = [self.row(self.installCheck, self.prepareCheck if system == "macos" else None),
                    ("Install folder", self.installDirectory)]
            if system == "linux":
                rows.append(self.row(self.desktopCheck))
            sections = (self.section(*rows),)
        self.platformBox = self.box(
            {"macos": "macOS", "windows": "Windows", "linux": "Linux"}.get(system, ""), *sections)
        self.manualSection = self.section(("Host", self.proxyHost), ("Port", self.proxyPort))
        self.proxyNotice = QLabel()
        self.proxyNotice.setWordWrap(True)
        self.proxyNotice.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.proxyNotice.hide()
        proxy = self.box("Proxy", self.section(("Mode", self.proxyMode)), self.manualSection, self.proxyNotice)

        width = max(label.sizeHint().width() for label in self.labels.values())
        for label in self.labels.values():
            label.setFixedWidth(width)
        refreshButton.setVisible(autoCheck.isChecked())
        checkButton.setVisible(not autoCheck.isChecked())

        settingsPanel = QWidget()
        layout = QVBoxLayout(settingsPanel)
        for box in (general, self.platformBox, proxy):
            layout.addWidget(box)
        resetButton = QPushButton("Reset settings…")
        resetButton.setToolTip(f"Hold for {self.RESET} seconds to restore defaults. Downloaded files will be kept.")
        resetButton.setFixedWidth(resetButton.sizeHint().width())
        clock = QElapsedTimer()
        timer = QTimer(resetButton)
        timer.setInterval(50)

        def countdown():
            if not resetButton.isDown() or not resetButton.isEnabled() or not resetButton.isVisible():
                timer.stop()
                resetButton.setText("Reset settings…")
            elif clock.elapsed() >= self.RESET * 1000:
                timer.stop()
                resetButton.setDown(False)
                self.reset()
            else:
                resetButton.setText(f"Reset in {self.RESET - clock.elapsed() // 1000}…")

        resetButton.pressed.connect(lambda: (clock.start(), resetButton.setText(f"Reset in {self.RESET}…"), timer.start()))
        resetButton.released.connect(lambda: (timer.stop(), resetButton.setText("Reset settings…")))
        timer.timeout.connect(countdown)
        layout.addWidget(resetButton, 0, Qt.AlignLeft)
        return settingsPanel

    def reset(self):
        if self.active:
            return
        self.settings.setting.clear()
        previous = self.settingsPanel
        visible = previous.isVisible()
        self.settingsPanel = self.panel(self.checkButton)
        self.takeCentralWidget()
        self.setCentralWidget(self.settingsPanel)
        self.settingsPanel.setVisible(visible)
        previous.hide()
        previous.deleteLater()
        self.apply()

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
        handled = super().event(event)
        if event.type() == QEvent.LayoutRequest:
            height = self.layout().totalHeightForWidth(self.width())
            self.setFixedHeight(height if height >= 0 else self.sizeHint().height())
        return handled

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
        browse = QToolButton()
        browse.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        browse.setToolTip("Choose folder")
        browse.setAccessibleName("Choose folder")
        browse.clicked.connect(lambda: edit.setText(directory) if (
            directory := QFileDialog.getExistingDirectory(self, "Choose folder", edit.text())
        ) else None)
        openButton = QToolButton()
        openButton.setIcon(self.style().standardIcon(QStyle.SP_DirLinkIcon))
        openButton.setToolTip("Open selected folder")
        openButton.setAccessibleName("Open selected folder")
        openButton.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(edit.text())))
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit)
        layout.addWidget(browse)
        layout.addWidget(openButton)
        return widget

    def apply(self):
        if hasattr(self, "updater"):
            proxy = self.proxy()
            if proxy != self.proxySelection:
                self.proxySelection = proxy
                self.proxyNotice.hide()
                self.proxied.emit(proxy)
            elif hasattr(self.updater, "route"):
                self.resolved()
        target = self.platformCombo.currentData() or self.platform.current()
        self.badge.setText(Platform.supported().get(target, "Unknown target"))
        native = self.platform.native(target)
        system = self.platform.system()
        windows = native and system == "windows"
        macos = native and system == "macos"
        exe = windows and self.packageCombo.currentData() == Platform.Package.EXE.name
        portable = windows and not exe
        install = native and self.installCheck.isChecked()
        manual = self.proxyMode.currentData() == Proxy.Mode.MANUAL.name
        self.installable = install
        self.platformBox.setVisible(native)
        if system == "windows":
            self.exeSection.setVisible(exe)
            self.zipSection.setVisible(not exe)
        for widget, enabled in (
                (self.installDirectory, install and not exe),
                (self.prepareCheck, install and macos),
                (self.systemCheck, install and exe),
                (self.desktopCheck, install and (portable or system == "linux"))):
            widget.setEnabled(enabled)
            if widget in self.labels:
                self.labels[widget].setEnabled(enabled)
        self.manualSection.setVisible(manual)
        self.installCheck.setText("Run Chromium installer" if exe else "Install Chromium")

        if install:
            action = "Download and Install"
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
        if self.active:
            return
        folder, file = self.remote
        if not folder:
            self.status.setText("No snapshot for this target")
            return
        self.work(True)
        self.url = ""
        self.urlButton.setEnabled(False)
        self.actionButton.setEnabled(False)
        self.queried.emit(folder, file, self.proxy())

    def changed(self):
        report = self.updater.report
        self.progress.setRange(0, report["total"])
        self.progress.setValue(report["received"])
        text = {Updater.Stage.CHECKING: "Checking…", Updater.Stage.DOWNLOADING: "Downloading…",
                Updater.Stage.EXTRACTING: "Extracting…", Updater.Stage.INSTALLING: "Installing…"}.get(report["stage"], "")
        self.actionButton.setEnabled(report["cancellable"] and report["stage"] != Updater.Stage.CHECKING)
        if report["total"]:
            text += f" {report['received'] >> 20} of {report['total'] >> 20} MB"
        if speed := report["speed"]:
            rate = f"{speed / (1 << 20):.1f} MB/s" if speed >= 1 << 20 else f"{speed >> 10} KB/s"
            text += f" · {rate}"
        self.status.setText(text)

    def act(self):
        if self.active:
            # A queued cancel cannot reach the busy extraction loop
            self.updater.cancelled = True
            self.aborted.emit()
            return
        self.work(True)
        self.requested.emit(self.proxy(), self.options())

    def proxy(self):
        return {"mode": self.proxyMode.currentData(),
                "host": self.proxyHost.text().strip(), "port": self.proxyPort.value()}

    def resolved(self):
        route = self.updater.route
        self.proxyNotice.setText(route["text"])
        color = "40, 167, 69" if route["valid"] else "220, 53, 69"
        self.proxyNotice.setStyleSheet(
            f"background-color: rgba({color}, 48);"
            f"border: 1px solid rgba({color}, 128);"
            "border-radius: 4px; padding: 4px;")
        self.proxyNotice.setVisible(route["mode"] != Proxy.Mode.DIRECT.name)

    def options(self):
        options = {option: self.settings.get(option) for option in (
            Settings.Option.DOWNLOADDIR, Settings.Option.SEGMENTS, Settings.Option.INSTALL,
            Settings.Option.INSTALLDIR, Settings.Option.MACSIGN,
            Settings.Option.WINSYSTEM, Settings.Option.WINDESKTOP, Settings.Option.LINUXDESKTOP)}
        options[Settings.Option.INSTALL] = self.installable
        return options

    def finished(self, result):
        report = self.updater.report
        if (report["folder"], report["file"]) != self.remote:
            return
        self.work(False)
        self.actionButton.setEnabled(bool(self.url))
        if result != Updater.Result.DONE:
            self.progress.setRange(0, 100)
            self.progress.reset()
            self.status.setText("Cancelled" if result == Updater.Result.CANCELLED else report["error"])
        elif report["stage"] == Updater.Stage.CHECKING:
            self.url = f"{Updater.SERVER}/{report['folder']}/{report['revision']}/{report['file']}"
            self.urlButton.setEnabled(True)
            self.actionButton.setEnabled(True)
            self.progress.setRange(0, 100)
            self.status.setText(f"Revision {report['revision']} is available")
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(100)
            done = "Installed" if report["stage"] == Updater.Stage.INSTALLING else "Downloaded"
            self.status.setText(f"{done} revision {report['revision']}")

    def closeEvent(self, event):
        if self.active and not self.updater.report["cancellable"]:
            event.ignore()
            return
        # Extraction loops inside its slot and only a direct flag reaches it
        self.updater.cancelled = True
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
