# ADB File Explorer
# Copyright (C) 2022  Azat Aldeshov
from typing import List

from app.core.configurations import Settings
from app.core.managers import ADBManager
from app.data.models import FileType, Device, File
from app.helpers.app_sandbox import (
    PACKAGE_DIR_PERMISSIONS,
    parse_packages,
    run_as_hint,
    sandbox_context,
    synthetic_children,
)
from app.helpers.converters import convert_to_devices, convert_to_file, convert_to_file_list_a
from app.helpers.tools import build_test_d_batch_script, parse_test_d_batch_output
from app.services import adb
import posixpath
import shlex


class FileRepository:
    @classmethod
    def file(cls, path: str) -> (File, str):
        if not ADBManager.get_device():
            return None, "No device selected!"

        path = ADBManager.clear_path(path)

        context = sandbox_context(path)
        if context:
            return cls.__sandbox_file(context)

        args = adb.ShellCommand.LS_LIST_DIRS + [path]
        response = adb.shell(ADBManager.get_device().id, [shlex.join(args)])
        if not response.IsSuccessful:
            if synthetic_children(path) is not None:
                return File(name=posixpath.basename(path), path=path,
                            permissions=PACKAGE_DIR_PERMISSIONS), None
            return None, response.ErrorData or response.OutputData

        file = convert_to_file(response.OutputData.strip())
        if not file:
            return None, "Unexpected string:\n%s" % response.OutputData

        if file.type == FileType.LINK:
            # Prefer exit-code-based check: test -d will dereference symlink
            test_args = ['sh', '-c', f"test -d {shlex.quote(path)}"]
            test_resp = adb.shell(ADBManager.get_device().id, [shlex.join(test_args)])
            file.link_type = FileType.DIRECTORY if test_resp.IsSuccessful else FileType.FILE
        file.path = path
        return file, response.ErrorData

    @classmethod
    def files(cls) -> (List[File], str):
        if not ADBManager.get_device():
            return None, "No device selected!"

        path = ADBManager.path()

        context = sandbox_context(path)
        if context:
            return cls.__sandbox_files(context, path)

        args = adb.ShellCommand.LS_ALL_LIST + [path]
        response = adb.shell(ADBManager.get_device().id, [shlex.join(args)])

        known = synthetic_children(path)
        if known and (not response.IsSuccessful or not response.OutputData):
            return [
                File(name=child, path=(path + child), permissions=PACKAGE_DIR_PERMISSIONS)
                for child in known
            ], None

        if not response.IsSuccessful and response.ExitCode != 1:
            return [], response.ErrorData or response.OutputData

        if not response.OutputData:
            return [], response.ErrorData

        # Build files list first, then resolve symlink directory types individually
        files = convert_to_file_list_a(response.OutputData, dirs=[], path=path)

        # Resolve link types without relying on globbing — batch all checks in a single shell call
        symlink_paths = []
        for f in files:
            if f.permissions and f.permissions[0] == 'l' and (f.link_type is None or f.link_type is FileType.FILE):
                check_path = f.path if getattr(f, 'path', None) else (path + f.name)
                symlink_paths.append(check_path)

        if symlink_paths:
            # Build one script to test all symlinks using shared helper; safely quotes each path
            script = build_test_d_batch_script(symlink_paths)
            cmd = shlex.join(['sh', '-c', script])
            batch_resp = adb.shell(ADBManager.get_device().id, [cmd])
            if batch_resp.IsSuccessful and batch_resp.OutputData:
                status = parse_test_d_batch_output(batch_resp.OutputData)

                for f in files:
                    if f.permissions and f.permissions[0] == 'l':
                        p = f.path if getattr(f, 'path', None) else (path + f.name)
                        if p in status:
                            f.link_type = FileType.DIRECTORY if status[p] else FileType.FILE
        return files, response.ErrorData

    # ------------------------------------------------------------------
    # App-private data (/data/data/<pkg>) support
    #
    # The shell user can't list /data/data on a production build, so mimic
    # Android Studio's Device Explorer: synthesize the package listing from
    # `pm list packages`, and read/write inside a package with `run-as`
    # (works for apps installed from a debuggable build).
    # ------------------------------------------------------------------
    @classmethod
    def __sandbox_file(cls, context) -> (File, str):
        device_id = ADBManager.get_device().id
        if context.is_root:
            return File(
                name=posixpath.basename(context.root),
                path=context.root,
                permissions=PACKAGE_DIR_PERMISSIONS,
            ), None

        args = adb.ShellCommand.LS_LIST_DIRS + [context.target]
        response = adb.shell_run_as(device_id, context.package, [shlex.join(args)])
        if not response.IsSuccessful:
            if context.inner:
                return None, run_as_hint(context.package, response.ErrorData or response.OutputData)
            # Package dir itself: still show it as a folder so the user can see
            # the "not debuggable" reason only when they try to open it.
            return File(
                name=context.package,
                path=context.package_dir,
                permissions=PACKAGE_DIR_PERMISSIONS,
            ), None

        file = convert_to_file((response.OutputData or '').strip())
        if not file:
            return None, "Unexpected string:\n%s" % response.OutputData
        file.path = context.target
        return file, None

    @classmethod
    def __sandbox_files(cls, context, path: str) -> (List[File], str):
        device_id = ADBManager.get_device().id

        if context.is_root:
            response = adb.list_packages(device_id)
            if not response.IsSuccessful:
                return [], response.ErrorData or response.OutputData
            files = [
                File(name=package, path=(path + package), permissions=PACKAGE_DIR_PERMISSIONS)
                for package in parse_packages(response.OutputData)
            ]
            return files, None

        args = adb.ShellCommand.LS_ALL_LIST + [context.target]
        response = adb.shell_run_as(device_id, context.package, [shlex.join(args)])
        if not response.IsSuccessful and response.ExitCode != 1:
            return [], run_as_hint(context.package, response.ErrorData or response.OutputData)
        if not response.OutputData:
            return [], response.ErrorData
        return convert_to_file_list_a(response.OutputData, dirs=[], path=path), None

    @classmethod
    def rename(cls, file: File, name) -> (str, str):
        if name.__contains__('/') or name.__contains__('\\'):
            return None, "Invalid name"

        args = [adb.ShellCommand.MV, file.path, (file.location + name)]
        context = sandbox_context(file.path)
        if context and not context.is_root:
            response = adb.shell_run_as(
                ADBManager.get_device().id, context.package, [shlex.join(args)]
            )
            if not response.IsSuccessful:
                return None, run_as_hint(context.package, response.ErrorData or response.OutputData)
            return None, response.OutputData
        response = adb.shell(ADBManager.get_device().id, [shlex.join(args)])
        return None, response.ErrorData or response.OutputData

    @classmethod
    def __transfer(cls, file: File, destination: str, move: bool) -> (str, str):
        """Copy or move ``file`` into the device directory ``destination``."""
        if not ADBManager.get_device():
            return None, "No device selected!"
        if not destination or not destination.startswith('/'):
            return None, "Enter an absolute destination path (starting with '/')."

        verb = "Move" if move else "Copy"
        done = "Moved" if move else "Copied"
        source = file.path
        target = posixpath.join(ADBManager.clear_path(destination), file.name)
        if posixpath.normpath(source) == posixpath.normpath(target):
            return None, "Source and destination are the same."

        base = [adb.ShellCommand.MV] if move else adb.ShellCommand.CP_RECURSIVE
        args = base + [source, target]

        src_ctx = sandbox_context(source)
        dst_ctx = sandbox_context(target)
        device_id = ADBManager.get_device().id

        dest_dir = ADBManager.clear_path(destination)
        check = [shlex.join(['sh', '-c', 'test -d %s' % shlex.quote(dest_dir)])]
        if dst_ctx and not dst_ctx.is_root:
            exists = adb.shell_run_as(device_id, dst_ctx.package, check).IsSuccessful
        else:
            exists = adb.shell(device_id, check).IsSuccessful
        if not exists:
            return None, "Destination folder '%s' does not exist on the device." % dest_dir

        if not src_ctx and not dst_ctx:
            response = adb.shell(device_id, [shlex.join(args)])
        elif (
            src_ctx and dst_ctx
            and not src_ctx.is_root and not dst_ctx.is_root
            and src_ctx.package == dst_ctx.package
        ):
            response = adb.shell_run_as(device_id, src_ctx.package, [shlex.join(args)])
        else:
            return None, (
                "%s between an app's private data and another location isn't "
                "supported over adb — use Download and then Upload instead." % verb
            )

        if not response.IsSuccessful or response.OutputData:
            hint = response.ErrorData or response.OutputData
            if src_ctx and not src_ctx.is_root:
                hint = run_as_hint(src_ctx.package, hint)
            return None, hint
        return "%s '%s' to '%s'" % (done, source, target), None

    @classmethod
    def copy(cls, file: File, destination: str) -> (str, str):
        return cls.__transfer(file, destination, move=False)

    @classmethod
    def move(cls, file: File, destination: str) -> (str, str):
        return cls.__transfer(file, destination, move=True)

    @classmethod
    def open_file(cls, file: File) -> (str, str):
        args = [adb.ShellCommand.CAT, file.path]
        if file.isdir:
            return None, "Can't open. %s is a directory" % file.path
        context = sandbox_context(file.path)
        if context and not context.is_root:
            response = adb.shell_run_as(
                ADBManager.get_device().id, context.package, [shlex.join(args)]
            )
            if not response.IsSuccessful:
                return None, run_as_hint(context.package, response.ErrorData or response.OutputData)
            return response.OutputData, None
        response = adb.shell(ADBManager.get_device().id, [shlex.join(args)])
        if not response.IsSuccessful:
            return None, response.ErrorData or response.OutputData
        return response.OutputData, response.ErrorData

    @classmethod
    def delete(cls, file: File) -> (str, str):
        args = [adb.ShellCommand.RM, file.path]
        if file.isdir:
            args = adb.ShellCommand.RM_DIR_FORCE + [file.path]
        context = sandbox_context(file.path)
        if context and not context.is_root:
            response = adb.shell_run_as(
                ADBManager.get_device().id, context.package, [shlex.join(args)]
            )
            if not response.IsSuccessful or response.OutputData:
                return None, run_as_hint(context.package, response.ErrorData or response.OutputData)
            return "%s '%s' has been deleted" % ('Folder' if file.isdir else 'File', file.path), None
        response = adb.shell(ADBManager.get_device().id, [shlex.join(args)])
        if not response.IsSuccessful or response.OutputData:
            return None, response.ErrorData or response.OutputData
        return "%s '%s' has been deleted" % ('Folder' if file.isdir else 'File', file.path), None

    class UpDownHelper:
        def __init__(self, callback: callable):
            self.messages = []
            self.callback = callback

        def call(self, data: str):
            if data.startswith('['):
                progress = data[1:4].strip()
                if progress.isdigit():
                    self.callback(data[7:], int(progress))
            elif data:
                self.messages.append(data)

    @classmethod
    def download(cls, progress_callback: callable, source: str, destination: str) -> (str, str):
        if not destination:
            destination = Settings.device_downloads_path(ADBManager.get_device())
        if ADBManager.get_device() and source and destination:
            device_id = ADBManager.get_device().id

            context = sandbox_context(source)
            if context and not context.is_root:
                stat = adb.shell_run_as(
                    device_id, context.package,
                    [shlex.join(adb.ShellCommand.LS_LIST_DIRS + [source])]
                )
                if not stat.IsSuccessful:
                    return None, run_as_hint(context.package, stat.ErrorData or stat.OutputData)
                stat_file = convert_to_file((stat.OutputData or '').strip())
                is_dir = bool(stat_file and stat_file.isdir)
                response = adb.sandbox_pull(device_id, context.package, source, destination, is_dir)
                if not response.IsSuccessful:
                    return None, run_as_hint(context.package, response.ErrorData)
                return response.OutputData, None

            helper = cls.UpDownHelper(progress_callback)
            response = adb.pull(device_id, source, destination, helper.call)
            if not response.IsSuccessful:
                return None, response.ErrorData or "\n".join(helper.messages)

            return "\n".join(helper.messages), response.ErrorData
        return None, None

    @classmethod
    def new_folder(cls, name) -> (str, str):
        if not ADBManager.get_device():
            return None, "No device selected!"

        target = ADBManager.path() + name
        context = sandbox_context(ADBManager.path())
        if context and not context.is_root:
            response = adb.shell_run_as(
                ADBManager.get_device().id, context.package,
                [shlex.join([adb.ShellCommand.MKDIR, target])]
            )
            if not response.IsSuccessful:
                return None, run_as_hint(context.package, response.ErrorData or response.OutputData)
            return response.OutputData, None

        args = [adb.ShellCommand.MKDIR, target]
        response = adb.shell(ADBManager.get_device().id, [shlex.join(args)])
        if not response.IsSuccessful:
            return None, response.ErrorData or response.OutputData
        return response.OutputData, response.ErrorData

    @classmethod
    def upload(cls, progress_callback: callable, source: str) -> (str, str):
        if ADBManager.get_device() and ADBManager.path() and source:
            device_id = ADBManager.get_device().id

            context = sandbox_context(ADBManager.path())
            if context and not context.is_root:
                response = adb.sandbox_push(device_id, context.package, source, context.target)
                if not response.IsSuccessful:
                    return None, run_as_hint(context.package, response.ErrorData)
                return response.OutputData, None

            helper = cls.UpDownHelper(progress_callback)
            response = adb.push(device_id, source, ADBManager.path(), helper.call)
            if not response.IsSuccessful:
                return None, response.ErrorData or "\n".join(helper.messages)

            return "\n".join(helper.messages), response.ErrorData
        return None, None


class DeviceRepository:
    @classmethod
    def devices(cls) -> (List[Device], str):
        response = adb.devices()
        if not response.IsSuccessful:
            return [], response.ErrorData or response.OutputData

        devices = convert_to_devices(response.OutputData)
        return devices, response.ErrorData

    @classmethod
    def connect(cls, device_id) -> (str, str):
        if not device_id:
            return None, None

        response = adb.connect(device_id)
        if not response.IsSuccessful:
            return None, response.ErrorData or response.OutputData
        return response.OutputData, response.ErrorData

    @classmethod
    def disconnect(cls) -> (str, str):
        response = adb.disconnect()
        if not response.IsSuccessful:
            return None, response.ErrorData or response.OutputData

        return response.OutputData, response.ErrorData
