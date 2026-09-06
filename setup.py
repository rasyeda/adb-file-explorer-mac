"""
Build a standalone macOS .app bundle for ADB File Explorer with py2app.

Run this ON macOS (py2app builds a native, platform-specific bundle and
cannot cross-compile from Linux/Windows):

    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    pip install py2app
    python setup.py py2app

Result: dist/ADB File Explorer.app -- drag it into /Applications.
"""
import os
import sys

from setuptools import setup

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")

# 'app' and 'resources' live under src/, not at repo root, so make them
# importable during py2app's static-analysis build step.
sys.path.insert(0, SRC)

APP = ["mac_main.py"]

# Version: overridable by CI from the git tag (e.g. tag "v1.5.0" -> "1.5.0").
VERSION = os.environ.get("ADBFE_VERSION", "1.5.0").lstrip("v") or "1.5.0"

ICONFILE = os.path.join(HERE, "AppIcon.icns")
HAS_ICON = os.path.isfile(ICONFILE)

OPTIONS = {
    "py2app": {
        # Bundle 'app' and 'resources' as real packages (folders on disk,
        # not flattened into the zipped stdlib) so pkg_resources.resource_filename()
        # can still find settings.json / .qss / .svg files inside them at runtime.
        "packages": ["app", "resources"],
        "includes": [
            "PyQt5.sip",
            # Optional python-native ADB backend (adb_core: "python" in
            # settings.json) -- safe to include even if unused.
            "adb_shell",
            "adb_shell.adb_device",
            "cryptography",
            "rsa",
            "pyasn1",
            "cffi",
            "usb1",
        ],
        "excludes": ["tkinter"],
        "argv_emulation": False,
        "iconfile": ICONFILE if HAS_ICON else None,
        "plist": {
            "CFBundleName": "ADB File Explorer",
            "CFBundleDisplayName": "ADB File Explorer",
            "CFBundleIdentifier": "com.aldeshov.adbfileexplorer",
            "CFBundleVersion": VERSION,
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "10.14",
            "NSHumanReadableCopyright": "Copyright (C) 2025 Azat Aldeshov. GPLv3.",
        },
    }
}

setup(
    app=APP,
    name="ADB File Explorer",
    version=VERSION,
    options=OPTIONS,
    setup_requires=["py2app"],
)
