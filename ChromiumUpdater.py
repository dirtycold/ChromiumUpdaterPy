"""Chromium Updater: a compact Qt window, built one step at a time."""

import sys
from enum import Enum

from qtpy.QtCore    import  QSettings, QStandardPaths, QSysInfo, Qt
from qtpy.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, 
                            QFormLayout, QGroupBox, QHBoxLayout, QLabel, 
                            QLayout, QLineEdit, QProgressBar, QPushButton, 
                            QSpinBox, QVBoxLayout, QWidget,
                            )


class Platform:
    class Directory(Enum):
        DOWNLOAD = 0
        INSTALL  = 1

    def __init__(self):
        self.system = {"darwin": "macos", "win32": "windows", "linux": "linux"}.get(sys.platform, "")
        self.architecture = {
            "arm64": "arm64", "aarch64": "arm64",
            "x86_64": "x64", "amd64": "x64",
            "i386": "x86", "i686": "x86", "x86": "x86",
        }.get(QSysInfo.currentCpuArchitecture().lower(), "")

    @staticmethod
    def supported():
        return {
            "macos-arm64": "macOS · ARM64",
            "macos-x64": "macOS · x64",
            "windows-arm64": "Windows · ARM64",
            "windows-x64": "Windows · x64",
            "windows-x86": "Windows · x86",
            "linux-arm64": "Linux · ARM64",
            "linux-x64": "Linux · x64",
        }

    def current(self):
        target = f"{self.system}-{self.architecture}"
        return target if target in Platform.supported() else ""

    def extract(self):
        return self.system in {"macos", "windows"}

    def compare(self, target):
        return bool(target) and target == self.current()

    def verify(self, target):
        return self.compare(target) and self.system == "macos"

    def directory(self, kind: Directory):
        if kind == self.Directory.DOWNLOAD:
            return QStandardPaths.writableLocation(QStandardPaths.CacheLocation)
        if kind != self.Directory.INSTALL:
            return ""
        home = QStandardPaths.writableLocation(QStandardPaths.HomeLocation)
        return {
            "macos": f"{home}/Applications",
            "windows": QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation) + "/Chromium",
            "linux": f"{home}/.local/opt/chromium",
        }.get(self.system, "")


class Proxy:
    class Mode(Enum):
        DIRECT = 0
        SYSTEM = 1
        MANUAL = 2


class Settings:
    class Option(Enum):
        PLATFORM    = "Platform"
        EXTRACT     = "Extract"
        INSTALL     = "Install"
        CLEANUP     = "Cleanup"
        DOWNLOADDIR = "Directory/Download"
        INSTALLDIR  = "Directory/Install"
        MACSIGNER   = "Mac/Sign"
        PROXYMODE   = "Proxy/Mode"
        PROXYHOST   = "Proxy/Host"
        PROXYPORT   = "Proxy/Port"

    def __init__(self, platform: Platform):
        self.setting = QSettings()
        defaults = {
            self.Option.PLATFORM:       platform.current(),
            self.Option.EXTRACT:        "true" if platform.extract() else "false",
            self.Option.INSTALL:        "true" if platform.extract() else "false",
            self.Option.CLEANUP:        "false",
            self.Option.DOWNLOADDIR:    platform.directory(Platform.Directory.DOWNLOAD),
            self.Option.INSTALLDIR:     platform.directory(Platform.Directory.INSTALL),
            self.Option.MACSIGNER:      "true",
            self.Option.PROXYMODE:      Proxy.Mode.SYSTEM.name,
            self.Option.PROXYHOST:      "",
            self.Option.PROXYPORT:      "8080",
        }
        for option, value in defaults.items():
            if not self.setting.contains(option.value):
                self.set(option, value)

    def get(self, option: Option) -> str:
        return self.setting.value(option.value, "", type=str)

    def set(self, option: Option, value: str):
        self.setting.setValue(option.value, str(value))


class Window(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chromium Updater")
        self.setFixedWidth(520)
        self.platform = Platform()
        self.settings = Settings(self.platform)
        target = self.settings.get(Settings.Option.PLATFORM)

        self.status = QLabel("Ready")
        self.targetBadge = QLabel()
        self.targetBadge.setToolTip("Selected download target")
        self.targetBadge.setStyleSheet(
            "border: 1px solid palette(mid); border-radius: 3px; padding: 2px 6px;")

        status = QHBoxLayout()
        status.addWidget(self.status, 1)
        status.addWidget(self.targetBadge, 0, Qt.AlignRight)

        checkButton = QPushButton("Check for Updates")
        self.actionButton = QPushButton("Download")
        urlButton = QPushButton("Copy URL")
        settingsButton = QPushButton("Settings")
        settingsButton.setCheckable(True)

        self.platformCombo = QComboBox()
        self.platformCombo.addItem("Auto", "")
        for value, label in Platform.supported().items():
            self.platformCombo.addItem(label, value)
        if target and target not in Platform.supported():
            self.platformCombo.addItem(f"Unknown target: {target}", target)
        self.platformCombo.setCurrentIndex(self.platformCombo.findData(target))
        self.platformCombo.currentIndexChanged.connect(lambda: (
            self.settings.set(Settings.Option.PLATFORM, self.platformCombo.currentData()),
            self.apply(),
        ))

        settingsPanel = QGroupBox("Settings")
        self.settingsLayout = QFormLayout(settingsPanel)
        self.settingsLayout.addRow("Platform", self.platformCombo)

        self.settingsLayout.addRow("Download folder", self.directory(Settings.Option.DOWNLOADDIR))

        self.extractCheck = QCheckBox("Extract downloaded archive")
        self.installCheck = QCheckBox("Install extracted Chromium")
        self.prepareCheck = QCheckBox("Prepare Chromium for macOS")
        self.prepareCheck.setToolTip(
            "Remove quarantine; repair only the outer app signature for the known resource error.")
        self.cleanupCheck = QCheckBox("Remove archive after successful installation")
        for checkbox, option in (
                (self.extractCheck, Settings.Option.EXTRACT),
                (self.installCheck, Settings.Option.INSTALL),
                (self.prepareCheck, Settings.Option.MACSIGNER),
                (self.cleanupCheck, Settings.Option.CLEANUP)):
            checkbox.setChecked(self.settings.get(option) == "true")
            checkbox.toggled.connect(lambda checked, key=option: (
                self.settings.set(key, "true" if checked else "false"),
                self.apply(),
            ))

        self.settingsLayout.addRow(self.extractCheck)
        self.settingsLayout.addRow(self.installCheck)
        self.installDirectory = self.directory(Settings.Option.INSTALLDIR)
        self.settingsLayout.addRow("Install folder", self.installDirectory)
        self.settingsLayout.addRow(self.prepareCheck)
        self.settingsLayout.addRow(self.cleanupCheck)

        self.proxyMode = QComboBox()
        for mode in Proxy.Mode:
            self.proxyMode.addItem(mode.name.title(), mode)
        try:
            mode = Proxy.Mode[self.settings.get(Settings.Option.PROXYMODE).upper()]
        except KeyError:
            mode = Proxy.Mode.SYSTEM
        self.proxyMode.setCurrentIndex(mode.value)
        self.proxyMode.currentIndexChanged.connect(lambda: (
            self.settings.set(Settings.Option.PROXYMODE, self.proxyMode.currentData().name),
            self.apply(),
        ))
        self.settingsLayout.addRow("Proxy", self.proxyMode)
        self.proxyHost = QLineEdit(self.settings.get(Settings.Option.PROXYHOST))
        self.proxyHost.setPlaceholderText("proxy.example.com")
        self.proxyHost.setToolTip("Hostname only; authenticated proxies are not supported yet")
        self.proxyHost.editingFinished.connect(
            lambda: self.settings.set(Settings.Option.PROXYHOST, self.proxyHost.text().strip()))
        self.settingsLayout.addRow("Proxy host", self.proxyHost)
        self.proxyPort = QSpinBox()
        self.proxyPort.setRange(1, 65535)
        try:
            port = int(self.settings.get(Settings.Option.PROXYPORT))
        except ValueError:
            port = 8080
        self.proxyPort.setValue(port)
        self.proxyPort.valueChanged.connect(
            lambda value: self.settings.set(Settings.Option.PROXYPORT, str(value)))
        self.settingsLayout.addRow("Proxy port", self.proxyPort)

        self.apply()
        settingsPanel.hide()
        settingsButton.toggled.connect(lambda visible: (
            settingsPanel.setVisible(visible),
            self.setFixedHeight(self.sizeHint().height()),
        ))

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
        layout.addWidget(settingsPanel)
        self.setFixedHeight(self.sizeHint().height())

    def directory(self, option):
        widget = QWidget()
        edit = QLineEdit(self.settings.get(option))
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
        local = self.platform.compare(target)
        extract = local and self.extractCheck.isChecked()
        install = extract and self.installCheck.isChecked()
        self.extractCheck.setEnabled(local)
        self.installCheck.setEnabled(extract)
        self.installDirectory.setEnabled(install)
        self.settingsLayout.labelForField(self.installDirectory).setEnabled(install)
        self.prepareCheck.setEnabled(extract and self.platform.verify(target))
        self.cleanupCheck.setEnabled(install)
        self.extractCheck.setToolTip(
            "Extract on this computer" if local else "Other targets are download only")

        manual = self.proxyMode.currentData() == Proxy.Mode.MANUAL
        for widget in (self.proxyHost, self.proxyPort):
            widget.setEnabled(manual)
            self.settingsLayout.labelForField(widget).setEnabled(manual)

        if install:
            action = "Download and Install"
        elif extract:
            action = "Download and Extract"
        else:
            action = "Download"
        self.actionButton.setText(action)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("ChromiumUpdaterPy")
    app.setOrganizationName("ChromiumUpdaterPy")
    window = Window()
    window.show()
    sys.exit(app.exec())
