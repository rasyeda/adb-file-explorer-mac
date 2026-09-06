# ADB File Explorer
# Copyright (C) 2022  Azat Aldeshov
"""
Helpers for browsing per-app private data directories (``/data/data/<pkg>``).

The ``shell`` user that ``adb`` runs as cannot list ``/data/data`` on a
production ("user" build) device, so a plain ``ls`` fails with
"Permission denied". Instead we do what Android Studio's Device Explorer
does:

* synthesize the directory listing of ``/data/data`` from
  ``pm list packages`` (the shell user *is* allowed to query the package
  manager), and
* read inside an individual package with ``run-as <pkg> ...``, which works
  for any app installed from a **debuggable** build.
"""
import posixpath
import re
from typing import List, Optional

# Directories whose immediate children are per-app private data dirs.
APP_DATA_ROOTS = ('/data/data', '/data/user/0')

# Displayed for the synthesized package folders. Mirrors the real mode of a
# freshly created app data dir (``rwxrwx--x``) so File.isdir/type work.
PACKAGE_DIR_PERMISSIONS = 'drwxrwx--x'


class SandboxContext:
    """Where a device path sits relative to an app-data root."""

    def __init__(self, root: str, package: Optional[str], inner: str):
        self.root = root            # e.g. '/data/data'
        self.package = package      # e.g. 'com.example.app', or None at the root
        self.inner = inner          # path inside the package dir, no leading '/'

    @property
    def is_root(self) -> bool:
        """True when the path is the app-data root itself (list packages)."""
        return not self.package

    @property
    def package_dir(self) -> str:
        return '%s/%s' % (self.root, self.package)

    @property
    def target(self) -> str:
        """Absolute on-device path this context points at."""
        if not self.package:
            return self.root
        if self.inner:
            return posixpath.join(self.package_dir, self.inner)
        return self.package_dir


def sandbox_context(path: str) -> Optional[SandboxContext]:
    """Return a SandboxContext if ``path`` is at/under an app-data root, else None."""
    if not path:
        return None
    norm = posixpath.normpath(path)
    for root in APP_DATA_ROOTS:
        if norm == root:
            return SandboxContext(root, None, '')
        prefix = root + '/'
        if norm.startswith(prefix):
            rest = norm[len(prefix):].strip('/')
            if not rest:
                return SandboxContext(root, None, '')
            parts = rest.split('/', 1)
            package = parts[0]
            inner = parts[1] if len(parts) > 1 else ''
            return SandboxContext(root, package, inner)
    return None


_PKG_RE = re.compile(r'^package:(.+)$')


def parse_packages(output: str) -> List[str]:
    """Parse ``pm list packages`` output into a sorted list of package names."""
    packages = set()
    for line in (output or '').splitlines():
        match = _PKG_RE.match(line.strip())
        if match:
            packages.add(match.group(1).strip())
    return sorted(packages)


# Parent dirs the shell user can't list either, but whose relevant children
# are well-known. Lets the user click down to /data/data instead of having to
# type the full path.
SYNTHETIC_CHILDREN = {
    '/data': ('data', 'user'),
}


def synthetic_children(path: str) -> Optional[List[str]]:
    """Known child names for a dir the shell user cannot list, else None."""
    if not path:
        return None
    return SYNTHETIC_CHILDREN.get(posixpath.normpath(path))


def run_as_hint(package: str, message: str) -> str:
    """Turn a raw ``run-as`` failure into something actionable for the user."""
    text = (message or '').strip()
    low = text.lower()
    if 'not debuggable' in low:
        return (
            "Package '%s' is not debuggable, so its private data cannot be "
            "opened over adb. Only apps installed from a debug build are "
            "accessible — this is the same limitation as Android Studio's "
            "Device Explorer." % package
        )
    if 'not an application' in low:
        return (
            "'%s' is a system component, not a regular app, so it has no "
            "openable private data directory." % package
        )
    if 'unknown package' in low or 'is unknown' in low:
        return "Package '%s' is not installed on this device." % package
    return text or ("run-as %s failed" % package)
