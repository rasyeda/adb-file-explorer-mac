#!/bin/bash
# Generates AppIcon.icns from src/resources/icons/logo.svg.
# Optional: if this isn't run, the app still builds fine with a default icon.
# Must run on macOS (uses `iconutil`, a macOS-only tool).
set -e
cd "$(dirname "$0")"

SVG="src/resources/icons/logo.svg"
ICONSET="AppIcon.iconset"
rm -rf "$ICONSET"
mkdir "$ICONSET"

python3 - "$SVG" "$ICONSET" <<'PYEOF'
import sys
from PyQt5.QtGui import QPainter, QImage, QColor
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtCore import Qt

svg_path, iconset_dir = sys.argv[1], sys.argv[2]

# Apple's required iconset filenames -> pixel size to render.
files = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}

renderer = QSvgRenderer(svg_path)
for name, size in files.items():
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    image.save(f"{iconset_dir}/{name}")
    print("wrote", name)
PYEOF

iconutil -c icns "$ICONSET" -o AppIcon.icns
echo "Created AppIcon.icns"
