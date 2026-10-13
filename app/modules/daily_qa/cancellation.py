"""현재 QA 실행의 신원을 확인하고 협력적 중단을 요청한다 (REQ-QAINTEL-036)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


class RunCancelled(RuntimeError):
    pass


class CheckedClient:
    """현재 읽기 요청 뒤 다음 Polarion 요청 전에 중단을 확인한다."""

    def __init__(self, client, check):
        self.client, self.check = client, check

    def iter_workitems(self, *args, **kwargs):
        iterator = iter(self.client.iter_workitems(*args, **kwargs))
        while True:
            self.check()
            try:
                item = next(iterator)
            except StopIteration:
                return
            self.check()
            yield item

    def get_comments(self, *args, **kwargs):
        self.check()
        result = self.client.get_comments(*args, **kwargs)
        self.check()
        return result

    def __getattr__(self, name):
        return getattr(self.client, name)


def _windows_command_line(kernel, handle) -> str | None:
    """같은 아키텍처의 문서화된 PEB·ProcessParameters만 읽는다. 실패하면 CIM을 쓴다.

    https://learn.microsoft.com/windows/win32/api/winternl/nf-winternl-ntqueryinformationprocess
    https://learn.microsoft.com/windows/win32/api/winternl/ns-winternl-peb
    https://learn.microsoft.com/windows/win32/api/winternl/ns-winternl-rtl_user_process_parameters
    """
    import ctypes
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [('Length', wintypes.USHORT), ('MaximumLength', wintypes.USHORT), ('Buffer', ctypes.c_void_p)]

    class BasicInformation(ctypes.Structure):
        _fields_ = [('Reserved1', ctypes.c_void_p), ('PebBaseAddress', ctypes.c_void_p),
                    ('Reserved2', ctypes.c_void_p * 2), ('UniqueProcessId', ctypes.c_size_t), ('Reserved3', ctypes.c_void_p)]

    class PebPrefix(ctypes.Structure):
        _fields_ = [('Reserved1', wintypes.BYTE * 2), ('BeingDebugged', wintypes.BYTE),
                    ('Reserved2', wintypes.BYTE), ('Reserved3', ctypes.c_void_p * 2),
                    ('Ldr', ctypes.c_void_p), ('ProcessParameters', ctypes.c_void_p)]

    class ProcessParameters(ctypes.Structure):
        _fields_ = [('Reserved1', wintypes.BYTE * 16), ('Reserved2', ctypes.c_void_p * 10),
                    ('ImagePathName', UnicodeString), ('CommandLine', UnicodeString)]

    try:
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.IsWow64Process2.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.USHORT), ctypes.POINTER(wintypes.USHORT)]
        machines = []
        for process in (kernel.GetCurrentProcess(), handle):
            machine, native = wintypes.USHORT(), wintypes.USHORT()
            if not kernel.IsWow64Process2(process, ctypes.byref(machine), ctypes.byref(native)):
                return None
            machines.append(machine.value or native.value)
        if machines[0] != machines[1]:
            return None
        query = ctypes.WinDLL('ntdll').NtQueryInformationProcess
        query.argtypes = [wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
        query.restype = wintypes.LONG
        info = BasicInformation()
        if query(handle, 0, ctypes.byref(info), ctypes.sizeof(info), None) < 0 or not info.PebBaseAddress:
            return None
        kernel.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]

        def read(address, target) -> bool:
            copied = ctypes.c_size_t()
            size = ctypes.sizeof(target)
            return bool(address and kernel.ReadProcessMemory(handle, address, ctypes.byref(target), size,
                                                             ctypes.byref(copied)) and copied.value == size)

        peb, parameters = PebPrefix(), ProcessParameters()
        if not read(info.PebBaseAddress, peb) or not read(peb.ProcessParameters, parameters):
            return None
        command = parameters.CommandLine
        if not command.Buffer or not command.Length or command.Length % 2 or command.Length > command.MaximumLength:
            return None
        # UNICODE_STRING.Length는 USHORT다. 원격 문자열의 지정된 길이만 복사하며 끝의 NUL을 읽지 않는다.
        buffer = ctypes.create_string_buffer(command.Length)
        if not read(command.Buffer, buffer):
            return None
        return buffer.raw.decode('utf-16-le')
    except (AttributeError, OSError, ValueError, UnicodeError):
        return None


def _windows_identity(pid: int, include_command: bool) -> dict | None:
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    # 메모리 읽기 권한이 없으면 기존 제한 조회 핸들과 CIM 확인으로 돌아간다.
    handle = kernel.OpenProcess(0x1410 if include_command and pid != os.getpid() else 0x1000, False, pid)
    if not handle and include_command:
        handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        creation, exit_time, system, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(system), ctypes.byref(user)):
            return None
        size = wintypes.DWORD(32768)
        image = ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
            return None
        argv = []
        if include_command:
            if pid == os.getpid():
                kernel.GetCommandLineW.restype = wintypes.LPWSTR
                command = kernel.GetCommandLineW()
            else:
                command = _windows_command_line(kernel, handle)
                if command is None:
                    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
                        f"(Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}').CommandLine"],
                        capture_output=True, text=True, encoding='utf-8', timeout=15,
                        creationflags=subprocess.CREATE_NO_WINDOW)
                    if result.returncode:
                        return None
                    command = result.stdout.strip()
            shell = ctypes.WinDLL('shell32', use_last_error=True)
            shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
            shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
            count = ctypes.c_int()
            values = shell.CommandLineToArgvW(command, ctypes.byref(count))
            if not values:
                return None
            try:
                argv = [values[index] for index in range(count.value)]
            finally:
                kernel.LocalFree.argtypes = [wintypes.HLOCAL]
                kernel.LocalFree(ctypes.cast(values, wintypes.HLOCAL))
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None
        return {'pid': pid, 'start': str((creation.dwHighDateTime << 32) | creation.dwLowDateTime),
                'executable': image.value, 'argv': argv}
    finally:
        kernel.CloseHandle(handle)


def process_identity(pid: int, include_command: bool = True) -> dict | None:
    """운영체제의 PID·시작 식별값·실행 명령을 읽는다. 확인 실패는 거절한다."""
    try:
        if pid <= 0:
            return None
        if sys.platform == 'win32':
            return _windows_identity(pid, include_command)
        proc = Path('/proc') / str(pid)
        stat = (proc / 'stat').read_text()
        start = stat.rsplit(')', 1)[1].split()[19]
        executable = str((proc / 'exe').resolve(strict=True))
        argv = [value.decode('utf-8', errors='replace') for value in (proc / 'cmdline').read_bytes().split(b'\0') if value] if include_command else []
        if (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19] != start:
            return None
        return {'pid': pid, 'start': start, 'executable': executable, 'argv': argv}
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def bind_lock(path: Path, run_id: str, product: str) -> None:
    identity = process_identity(os.getpid(), include_command=False) or {}
    path.write_text(json.dumps({'pid': os.getpid(), 'start': identity.get('start', ''),
                                'run_id': run_id, 'product': product}), encoding='utf-8')


def validate_owner(cfg, run_id: str) -> bool:
    try:
        before = cfg.lock_path.read_text(encoding='utf-8')
        owner = json.loads(before)
        if not isinstance(owner, dict) or owner.get('run_id') != run_id or owner.get('product') != cfg.slug:
            return False
        pid = owner.get('pid')
        if not isinstance(pid, int) or isinstance(pid, bool) or not owner.get('start'):
            return False
        identity = process_identity(pid)
        if not identity or identity['pid'] != pid or identity['start'] != owner['start']:
            return False
        executable = Path(identity['executable']).name.lower()
        if not executable.startswith(('python', 'pypy')):
            return False
        argv = identity.get('argv') or []
        script = cfg.root / 'scripts/run_daily_qa.py'
        if len(argv) < 4 or Path(argv[1]).resolve() != script.resolve() or '--product' not in argv:
            return False
        index = argv.index('--product') + 1
        if index >= len(argv) or argv[index].casefold() not in (cfg.product.casefold(), cfg.slug.casefold()):
            return False
        return before == cfg.lock_path.read_text(encoding='utf-8')
    except (OSError, ValueError, TypeError, KeyError):
        return False
