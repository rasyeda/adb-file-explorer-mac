# ADB File Explorer — macOS app bootstrap
#
# Why this file exists:
# The upstream src/app/__main__.py is designed to be run as
# `python3 ./src/app` with PYTHONPATH containing src/app *and* src.
# That two-path trick doesn't translate cleanly into a frozen py2app
# bundle. Every other module in the project already uses fully
# qualified `app.xxx` imports, so this script does the same thing
# __main__.py does, just with those qualified imports, and only
# needs `src` on sys.path (which setup.py arranges via `packages`).
import os
import sys

# --- Make `adb` findable ---------------------------------------------------
# Apps launched from Finder/Launchpad get a minimal PATH
# (/usr/bin:/bin:/usr/sbin:/sbin) that does NOT include Homebrew or the
# Android SDK's platform-tools, even if your Terminal's PATH does. Extend
# PATH here with the common install locations so the bundled app finds adb
# the same way your shell does.
_candidate_dirs = [
    "/opt/homebrew/bin",                                   # Homebrew (Apple Silicon)
    "/usr/local/bin",                                      # Homebrew (Intel)
    os.path.expanduser("~/Library/Android/sdk/platform-tools"),  # Android Studio default
    os.path.expanduser("~/Android/Sdk/platform-tools"),
]
os.environ["PATH"] = os.pathsep.join(_candidate_dirs + [os.environ.get("PATH", "")])

for _env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
    _sdk = os.environ.get(_env)
    if _sdk:
        _pt = os.path.join(_sdk, "platform-tools")
        if os.path.isdir(_pt):
            os.environ["PATH"] = _pt + os.pathsep + os.environ["PATH"]

# --- Run the app -------------------------------------------------------
from PyQt5.QtWidgets import QApplication

from app.core.configurations import Application
from app.core.main import Adb
from app.gui.window import MainWindow


def main():
    Application()
    Adb.start()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(app.font())

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
