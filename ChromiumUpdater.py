"""Chromium Updater: a compact Qt window, built one step at a time."""

import sys
from enum import Enum
from pathlib import Path

from qtpy.QtCore    import  QEvent, QSettings, QStandardPaths, QSysInfo, Qt
from qtpy.QtGui     import  QIcon
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


class Proxy:
    class Mode(Enum):
        DIRECT = 0
        SYSTEM = 1
        MANUAL = 2


class Settings:
    class Option(Enum):
        PLATFORM    = "Platform"
        AUTOCHECK   = "AutoCheck"
        EXTRACT     = "Extract"
        INSTALL     = "Install"
        CLEANUP     = "Cleanup"
        DOWNLOADDIR = "Directory/Download"
        INSTALLDIR  = "Directory/Install"
        MACSIGNER   = "Mac/Sign"
        WINPACKAGE  = "Win/Package"
        WINSYSTEM   = "Win/System"
        DESKTOP     = "Win/Desktop"
        PROXYMODE   = "Proxy/Mode"
        PROXYHOST   = "Proxy/Host"
        PROXYPORT   = "Proxy/Port"

    def __init__(self, platform: Platform):
        self.setting = QSettings()
        self.defaults = {
            self.Option.PLATFORM:       platform.current(),
            self.Option.AUTOCHECK:      False,
            self.Option.EXTRACT:        platform.system() in {"macos", "windows"},
            self.Option.INSTALL:        platform.system() in {"macos", "windows"},
            self.Option.CLEANUP:        False,
            self.Option.DOWNLOADDIR:    platform.directory(Platform.Directory.DOWNLOAD),
            self.Option.INSTALLDIR:     platform.directory(Platform.Directory.INSTALL),
            self.Option.MACSIGNER:      True,
            self.Option.WINPACKAGE:     Platform.Package.EXE.name,
            self.Option.WINSYSTEM:      False,
            self.Option.DESKTOP:        False,
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


class Window(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chromium Updater")
        self.setFixedWidth(520)
        self.platform = Platform()
        self.settings = Settings(self.platform)

        self.status = QLabel("Ready")
        self.targetBadge = QLabel()
        self.targetBadge.setToolTip("Selected download target")
        self.targetBadge.setStyleSheet(
            "border: 1px solid palette(mid); border-radius: 3px; padding: 2px 6px;")
        self.targetBadge.setFixedHeight(self.targetBadge.sizeHint().height())

        status = QHBoxLayout()
        status.addWidget(self.status, 1)
        status.addWidget(self.targetBadge, 0, Qt.AlignRight)

        checkButton = QPushButton("Check for Updates")
        self.actionButton = QPushButton("Download")
        urlButton = QPushButton("Copy URL")
        settingsButton = QPushButton("Settings")
        settingsButton.setCheckable(True)

        self.progress = QProgressBar()

        progress = QHBoxLayout()
        progress.addWidget(self.progress)
        progress.addWidget(urlButton)

        actions = QHBoxLayout()
        actions.addWidget(checkButton)
        actions.addWidget(self.actionButton)
        actions.addStretch()
        actions.addWidget(settingsButton)

        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SetNoConstraint)
        layout.addLayout(status)
        layout.addLayout(progress)
        layout.addLayout(actions)
        settingsPanel = self.panel(checkButton)
        settingsPanel.hide()
        layout.addWidget(settingsPanel)
        settingsButton.toggled.connect(settingsPanel.setVisible)
        self.apply()

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
        self.cleanupCheck = QCheckBox("Remove download after setup")
        self.cleanupCheck.setToolTip("Only after the requested extraction or installation succeeds")
        self.systemCheck = QCheckBox("Install system-wide for Windows")
        self.systemCheck.setToolTip("Windows EXE installer: --system-level; requires administrator approval")
        self.desktopCheck = QCheckBox("Create desktop shortcut for Windows")
        self.desktopCheck.setToolTip("Windows ZIP extraction only")
        for checkbox, option in (
                (autoCheck, Settings.Option.AUTOCHECK),
                (self.extractCheck, Settings.Option.EXTRACT),
                (self.installCheck, Settings.Option.INSTALL),
                (self.prepareCheck, Settings.Option.MACSIGNER),
                (self.cleanupCheck, Settings.Option.CLEANUP),
                (self.systemCheck,  Settings.Option.WINSYSTEM),
                (self.desktopCheck, Settings.Option.DESKTOP)):
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
        self.proxyPort.setRange(1, 65535)
        self.proxyPort.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.proxyPort.setValue(self.settings.get(Settings.Option.PROXYPORT))
        self.proxyPort.valueChanged.connect(
            lambda value: self.settings.set(Settings.Option.PROXYPORT, value))

        # Qt 5 forms keep the spacing of hidden rows, so whatever hides is a
        # section of its own; only this system's options are added at all
        self.labels = {}
        system = self.platform.system()
        general = self.box("General", self.section(
            ("Platform", self.platformCombo),
            ("Download folder", self.directory(Settings.Option.DOWNLOADDIR)),
            self.row(autoCheck, refreshButton)))
        self.installDirectory = self.directory(Settings.Option.INSTALLDIR)
        if system == "windows":
            self.exeSection = self.section(self.row(self.installCheck), self.row(self.systemCheck))
            self.zipSection = self.section(self.row(self.extractCheck),
                                           ("Install folder", self.installDirectory),
                                           self.row(self.desktopCheck))
            sections = (self.section(("Package", self.packageCombo)), self.exeSection,
                        self.zipSection, self.section(self.row(self.cleanupCheck)))
        else:
            rows = [self.row(self.extractCheck), self.row(self.installCheck),
                    ("Install folder", self.installDirectory), self.row(self.cleanupCheck)]
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
                (self.cleanupCheck, install or (portable and extract)),
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
        self.actionButton.setText(action)


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
