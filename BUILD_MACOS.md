# Building the macOS app

This has to be run **on a Mac** — `py2app` produces a native bundle tied to
the machine's Python/Qt binaries, so it can't be cross-built from Linux.

## 1. Prerequisites
- Xcode Command Line Tools: `xcode-select --install` (if not already installed)
- Python 3.9–3.11 (PyQt5 5.15 wheels don't have builds for very new Python
  versions yet — if `pip install` fails on requirements.txt, install 3.11 via
  `brew install python@3.11` and use that instead of your system Python)
- Android SDK platform-tools installed somewhere, so a real `adb` exists on
  disk (Android Studio installs this automatically). You don't need Android
  Studio itself, just `adb`.

## 2. Set up and build
```bash
cd ADBFileExplorer-mac
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install py2app

# Optional: generate the app icon from the project's own logo.
# Safe to skip -- the app builds fine without it.
chmod +x make_icon.sh
./make_icon.sh

python setup.py py2app
```

If it succeeds, you'll have:
```
dist/ADB File Explorer.app
```
Drag that into `/Applications`, or just double-click it from `dist/`.

## 3. First launch
Since the app isn't notarized/signed with a paid Apple Developer cert,
Gatekeeper will block the first launch. Either:
- Right-click the app → **Open** → **Open** (in the dialog), or
- `System Settings → Privacy & Security` → click **Open Anyway** after the
  first blocked attempt.

You only need to do this once.

## 4. If it can't find `adb`
The app extends its own `PATH` at startup to check the common install
locations (Homebrew, `~/Library/Android/sdk/platform-tools`,
`$ANDROID_HOME`). If your `adb` lives somewhere unusual, open:
```
~/Library/Application Support/ADB File Explorer/settings.json
```
and set:
```json
{ "adb_path": "/full/path/to/adb" }
```

## 5. Rebuilding after changes
```bash
rm -rf build dist
python setup.py py2app
```

## Troubleshooting
- **`ModuleNotFoundError` at launch**: py2app's static analysis can miss
  dynamically-imported modules. Add the missing module name to the
  `"includes"` list in `setup.py`, then rebuild (clean `build/` and `dist/`
  first).
- **App opens then instantly quits**: run the binary directly from Terminal
  to see the real traceback:
  `"dist/ADB File Explorer.app/Contents/MacOS/ADB File Explorer"`
- **PyQt5 install fails on your Python version**: PyQt5 5.15.x wheels stop
  around Python 3.11. Use `brew install python@3.11` and rebuild the venv
  with that interpreter.
