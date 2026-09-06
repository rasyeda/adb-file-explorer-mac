# ADB File Explorer
# Copyright (C) 2022  Azat Aldeshov
import os
import posixpath
import shlex
import subprocess
import tarfile

from app.core.configurations import Settings
from app.helpers.tools import CommonProcess

ADB_PATH = Settings.adb_path()
RUN_AS_ROOT = Settings.adb_run_as_root()
PRESERVE_TIMESTAMP = Settings.preserve_timestamp()


class Parameter:
    ROOT = 'root'
    DEVICE = '-s'
    PULL = 'pull'
    PUSH = 'push'
    SHELL = 'shell'
    CONNECT = 'connect'
    HELP = '--help'
    VERSION = '--version'
    DEVICES = 'devices'
    DEVICES_LONG = '-l'
    PRESERVE_TIMESTAMP = '-a'
    DISCONNECT = 'disconnect'
    START_SERVER = 'start-server'
    KILL_SERVER = 'kill-server'
    EXEC_OUT = 'exec-out'
    RUN_AS = 'run-as'


class ShellCommand:
    LS = 'ls'
    LS_ALL = [LS, '-a']
    LS_DIRS = [LS, '-d']
    LS_LIST = [LS, '-l']
    LS_LIST_DIRS = [LS, '-l', '-d']
    LS_ALL_DIRS = [LS, '-a', '-d']
    LS_ALL_LIST = [LS, '-a', '-l']
    LS_ALL_LIST_DIRS = [LS, '-a', '-l', '-d']
    LS_VERSION = [LS, '--version']

    CP = 'cp'
    CP_RECURSIVE = [CP, '-r']
    MV = 'mv'
    RM = 'rm'
    RM_DIR = [RM, '-r']
    RM_DIR_FORCE = [RM, '-r', '-f']

    GETPROP = 'getprop'
    GETPROP_PRODUCT_MODEL = [GETPROP, 'ro.product.model']

    MKDIR = 'mkdir'

    CAT = 'cat'

    PM_LIST_PACKAGES = ['pm', 'list', 'packages']


def validate():
    return version().IsSuccessful


def version():
    return CommonProcess([ADB_PATH, Parameter.VERSION])


def devices():
    return CommonProcess([ADB_PATH, Parameter.DEVICES, Parameter.DEVICES_LONG])


def start_server():
    return CommonProcess([ADB_PATH, Parameter.START_SERVER])


def kill_server():
    return CommonProcess([ADB_PATH, Parameter.KILL_SERVER])


def connect(device_id: str):
    return CommonProcess([ADB_PATH, Parameter.CONNECT, device_id])


def disconnect():
    return CommonProcess([ADB_PATH, Parameter.DISCONNECT])


def pull(device_id: str, source_path: str, destination_path: str, stdout_callback: callable):
    pull_options = [Parameter.PULL, Parameter.PRESERVE_TIMESTAMP] if PRESERVE_TIMESTAMP else [Parameter.PULL]
    args = [ADB_PATH, Parameter.DEVICE, device_id, *pull_options, source_path, destination_path]
    return CommonProcess(arguments=args, stdout_callback=stdout_callback)


def push(device_id: str, source_path: str, destination_path: str, stdout_callback: callable):
    args = [ADB_PATH, Parameter.DEVICE, device_id, Parameter.PUSH, source_path, destination_path]
    return CommonProcess(arguments=args, stdout_callback=stdout_callback)


def shell(device_id: str, args: list):
    if RUN_AS_ROOT:
        return CommonProcess([ADB_PATH, Parameter.DEVICE, device_id, Parameter.ROOT] + args)
    return CommonProcess([ADB_PATH, Parameter.DEVICE, device_id, Parameter.SHELL] + args)


def list_packages(device_id: str):
    args = [ADB_PATH, Parameter.DEVICE, device_id, Parameter.SHELL] + ShellCommand.PM_LIST_PACKAGES
    return CommonProcess(args)


def shell_run_as(device_id: str, package: str, args: list):
    """Run a shell command inside an app's sandbox via ``run-as <package>``.

    Works only for apps installed from a debuggable build (same constraint as
    Android Studio's Device Explorer). ``args`` is a list with a single already
    shell-joined string, matching how ``shell()`` is called elsewhere.
    """
    prefix = [ADB_PATH, Parameter.DEVICE, device_id, Parameter.SHELL, Parameter.RUN_AS, package]
    return CommonProcess(prefix + args)


def _safe_extract(tar: tarfile.TarFile, dest_dir: str):
    dest_root = os.path.realpath(dest_dir)
    for member in tar:
        member_path = os.path.realpath(os.path.join(dest_dir, member.name))
        if member_path != dest_root and not member_path.startswith(dest_root + os.sep):
            raise IOError("Blocked path traversal in archive member: %s" % member.name)
    tar.extractall(dest_dir)


class _Result:
    """Minimal CommonProcess-shaped result for the sandbox transfer helpers."""

    def __init__(self, ok: bool, output: str = None, error: str = None):
        self.IsSuccessful = ok
        self.OutputData = output
        self.ErrorData = error
        self.ExitCode = 0 if ok else 1


def sandbox_pull(device_id: str, package: str, remote_path: str, local_dir: str, is_dir: bool):
    """Copy a file or directory out of an app sandbox using ``run-as``.

    Regular ``adb pull`` can't read ``/data/data/<pkg>/...`` as the shell user,
    so stream the bytes through ``adb exec-out run-as <pkg> ...`` instead.
    """
    name = posixpath.basename(remote_path.rstrip('/')) or package
    try:
        os.makedirs(local_dir, exist_ok=True)
    except OSError as error:
        return _Result(False, error=str(error))

    if is_dir:
        parent = posixpath.dirname(remote_path.rstrip('/'))
        cmd = [ADB_PATH, Parameter.DEVICE, device_id, Parameter.EXEC_OUT,
               Parameter.RUN_AS, package, 'tar', '-c', '-C', parent, name]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            with tarfile.open(fileobj=process.stdout, mode='r|') as tar:
                _safe_extract(tar, local_dir)
        except Exception as error:  # noqa: BLE001 - report any archive failure
            process.stdout.close()
            process.wait()
            stderr = process.stderr.read().decode('utf-8', 'replace').strip()
            return _Result(False, error=stderr or str(error))
        process.wait()
        if process.returncode != 0:
            stderr = process.stderr.read().decode('utf-8', 'replace').strip()
            return _Result(False, error=stderr or 'run-as tar failed')
        return _Result(True, output='%s -> %s' % (remote_path, os.path.join(local_dir, name)))

    destination = os.path.join(local_dir, name)
    cmd = [ADB_PATH, Parameter.DEVICE, device_id, Parameter.EXEC_OUT,
           Parameter.RUN_AS, package, 'toybox', 'cat', remote_path]
    try:
        with open(destination, 'wb') as handle:
            process = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.PIPE)
            _, stderr = process.communicate()
    except OSError as error:
        return _Result(False, error=str(error))
    if process.returncode != 0:
        if os.path.exists(destination):
            os.remove(destination)
        return _Result(False, error=stderr.decode('utf-8', 'replace').strip() or 'run-as cat failed')
    return _Result(True, output='%s -> %s' % (remote_path, destination))


def sandbox_push(device_id: str, package: str, source_path: str, remote_dir: str):
    """Upload a local file/dir into an app sandbox via a ``/data/local/tmp`` hop."""
    name = os.path.basename(source_path.rstrip('/\\'))
    staging = '/data/local/tmp/%s' % name
    push = CommonProcess([ADB_PATH, Parameter.DEVICE, device_id, Parameter.PUSH, source_path, staging])
    if not push.IsSuccessful:
        return _Result(False, error=push.ErrorData or push.OutputData)

    target = posixpath.join(remote_dir, name)
    script = 'cp -r %s %s' % (shlex.quote(staging), shlex.quote(target))
    copy = shell_run_as(device_id, package, [script])
    CommonProcess([ADB_PATH, Parameter.DEVICE, device_id, Parameter.SHELL,
                   'rm', '-rf', staging])
    if not copy.IsSuccessful:
        return _Result(False, error=copy.ErrorData or copy.OutputData)
    return _Result(True, output='%s -> %s' % (source_path, target))


def file_list(device_id: str, path: str):
    return CommonProcess([ADB_PATH, Parameter.DEVICE, device_id, ShellCommand.LS, path])


def read_file(device_id: str, path: str):
    return CommonProcess([ADB_PATH, Parameter.DEVICE, device_id, ShellCommand.CAT, path])
