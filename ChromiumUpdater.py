"""Chromium Updater: a compact Qt window, built one step at a time."""

import sys
from enum import Enum

from qtpy.QtCore    import  QSettings, QStandardPaths, QSysInfo, QTimer, Qt
from qtpy.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, 
                            QFormLayout, QGroupBox, QHBoxLayout, QLabel, 
                            QLayout, QLineEdit, QProgressBar, QPushButton, 
                            QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
                            )


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
        defaults = {
            self.Option.PLATFORM:       platform.current(),
            self.Option.AUTOCHECK:      "false",
            self.Option.EXTRACT:        "true" if platform.system() in {"macos", "windows"} else "false",
            self.Option.INSTALL:        "true" if platform.system() in {"macos", "windows"} else "false",
            self.Option.CLEANUP:        "false",
            self.Option.DOWNLOADDIR:    platform.directory(Platform.Directory.DOWNLOAD),
            self.Option.INSTALLDIR:     platform.directory(Platform.Directory.INSTALL),
            self.Option.MACSIGNER:      "true",
            self.Option.WINPACKAGE:     Platform.Package.EXE.name,
            self.Option.WINSYSTEM:      "false",
            self.Option.DESKTOP:        "false",
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
        self.targetBadge.setFixedHeight(self.targetBadge.sizeHint().height())

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
        self.settingsLayout.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.settingsLayout.setLabelAlignment(Qt.AlignLeft)
        self.settingsLayout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.settingsLayout.addRow("Platform", self.platformCombo)

        self.settingsLayout.addRow("Download folder", self.directory(Settings.Option.DOWNLOADDIR))

        self.packageCombo = QComboBox()
        self.packageCombo.addItem("EXE installer", Platform.Package.EXE)
        self.packageCombo.addItem("ZIP archive", Platform.Package.ZIP)
        try:
            package = Platform.Package[self.settings.get(Settings.Option.WINPACKAGE).upper()]
        except KeyError:
            package = Platform.Package.EXE
        self.packageCombo.setCurrentIndex(package.value)
        self.packageCombo.currentIndexChanged.connect(lambda: (
            self.settings.set(Settings.Option.WINPACKAGE, self.packageCombo.currentData().name),
            self.apply(),
        ))

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
                (self.systemCheck, Settings.Option.WINSYSTEM),
                (self.desktopCheck, Settings.Option.DESKTOP)):
            checkbox.setChecked(self.settings.get(option) == "true")
            checkbox.toggled.connect(lambda checked, key=option: (
                self.settings.set(key, "true" if checked else "false"),
                self.apply(),
            ))

        refreshButton = QPushButton("Manual refresh")
        refreshButton.setToolTip("Check for updates now")
        refreshButton.clicked.connect(checkButton.click)
        autoCheck.toggled.connect(refreshButton.setVisible)
        self.settingsLayout.addRow(self.row(autoCheck, refreshButton))
        refreshButton.setVisible(autoCheck.isChecked())
        self.settingsLayout.addRow(self.row(self.extractCheck))
        self.settingsLayout.addRow(self.row(self.installCheck))
        self.installDirectory = self.directory(Settings.Option.INSTALLDIR)
        self.settingsLayout.addRow("Install folder", self.installDirectory)
        self.settingsLayout.addRow(self.row(self.cleanupCheck))

        self.settingsLayout.addRow(self.row(self.prepareCheck))
        self.settingsLayout.addRow("Windows package", self.packageCombo)
        self.settingsLayout.addRow(self.row(self.systemCheck))
        self.settingsLayout.addRow(self.row(self.desktopCheck))

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
        self.proxyHost.setMinimumWidth(240)
        self.proxyHost.setPlaceholderText("proxy.example.com")
        self.proxyHost.setToolTip("Hostname only; authenticated proxies are not supported yet")
        self.proxyHost.editingFinished.connect(
            lambda: self.settings.set(Settings.Option.PROXYHOST, self.proxyHost.text().strip()))
        self.settingsLayout.addRow("Proxy host", self.proxyHost)
        self.proxyPort = QSpinBox()
        self.proxyPort.setRange(1, 65535)
        self.proxyPort.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        try:
            port = int(self.settings.get(Settings.Option.PROXYPORT))
        except ValueError:
            port = 8080
        self.proxyPort.setValue(port)
        self.proxyPort.valueChanged.connect(
            lambda value: self.settings.set(Settings.Option.PROXYPORT, str(value)))
        self.settingsLayout.addRow("Proxy port", self.proxyPort)

        for combo in (self.platformCombo, self.packageCombo, self.proxyMode):
            combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        settingsPanel.hide()
        settingsButton.toggled.connect(lambda visible: (
            settingsPanel.setVisible(visible),
            QTimer.singleShot(0, lambda: self.setFixedHeight(self.sizeHint().height())),
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
        autoCheck.toggled.connect(lambda enabled: checkButton.setVisible(not enabled))
        checkButton.setVisible(not autoCheck.isChecked())
        self.apply()

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
        exe = windows and self.packageCombo.currentData() == Platform.Package.EXE
        portable = windows and not exe
        extract = native and not exe and self.extractCheck.isChecked()
        install = native and not portable and (exe or extract) and self.installCheck.isChecked()
        destination = (install and not exe) or (portable and extract)
        manual = self.proxyMode.currentData() == Proxy.Mode.MANUAL
        for widget, applicable in (
                (self.extractCheck.parentWidget(), native and not exe),
                (self.installCheck.parentWidget(), native and not portable and (exe or extract)),
                (self.installDirectory, destination),
                (self.cleanupCheck.parentWidget(), install or (portable and extract)),
                (self.prepareCheck.parentWidget(), extract and macos),
                (self.packageCombo, windows),
                (self.systemCheck.parentWidget(), install and exe),
                (self.desktopCheck.parentWidget(), portable and extract),
                (self.proxyHost, manual),
                (self.proxyPort, manual)):
            widget.setVisible(applicable)
            widget.setEnabled(applicable)
            label = self.settingsLayout.labelForField(widget)
            if label is not None:
                label.setVisible(applicable)
                label.setEnabled(applicable)
        self.installCheck.setText("Run Chromium installer" if exe else "Install extracted Chromium")

        if install:
            action = "Download and Install"
        elif extract:
            action = "Download and Extract"
        else:
            action = "Download"
        self.actionButton.setText(action)
        QTimer.singleShot(0, lambda: self.setFixedHeight(self.sizeHint().height()))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("ChromiumUpdaterPy")
    app.setOrganizationName("ChromiumUpdaterPy")
    window = Window()
    window.show()
    sys.exit(app.exec())
