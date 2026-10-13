"""Windows 실행기가 띄운 CLI와 그 자식만 묶는다 (REQ-QAINTEL-036).

공식 API: CreateJobObjectW / AssignProcessToJobObject / TerminateJobObject,
CreateToolhelp32Snapshot / Thread32First / OpenThread / GetProcessIdOfThread / ResumeThread.
https://learn.microsoft.com/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject
https://learn.microsoft.com/windows/win32/procthread/process-creation-flags
"""

import ctypes
import logging
from ctypes import wintypes

logger = logging.getLogger("regression_analyzer")
CREATE_SUSPENDED = 0x00000004


class WindowsJob:
    def __init__(self, process):
        self.process = process
        self.handle = None
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            'AssignProcessToJobObject': ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            'SetInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            'TerminateJobObject': ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
            'CreateToolhelp32Snapshot': ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            'OpenThread': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            'GetProcessIdOfThread': ([wintypes.HANDLE], wintypes.DWORD),
            'ResumeThread': ([wintypes.HANDLE], wintypes.DWORD),
            'GetExitCodeProcess': ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            'GetProcessTimes': ([wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result

    def attach(self):
        handle = self.kernel.CreateJobObjectW(None, None)
        if handle and self._kill_on_close(handle) and self.kernel.AssignProcessToJobObject(handle, int(self.process._handle)):
            self.handle = handle
            return True
        error = ctypes.get_last_error()
        if handle:
            self.kernel.CloseHandle(handle)
        logger.warning("qa_agent_windows_job_unavailable pid=%s error=%s action=refuse_start", self.process.pid, error)
        return False

    def _kill_on_close(self, handle):
        # Microsoft JOBOBJECT_EXTENDED_LIMIT_INFORMATION / JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.
        # https://learn.microsoft.com/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information
        class BasicLimits(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong), ('PerJobUserTimeLimit', ctypes.c_longlong),
                        ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', BasicLimits), ('IoInfo', ctypes.c_ulonglong * 6),
                        ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                        ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x00002000
        return bool(self.kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))

    def _identity(self):
        code = wintypes.DWORD()
        values = [wintypes.FILETIME() for _ in range(4)]
        handle = int(self.process._handle)
        if not self.kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None
        if not self.kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
            return None
        return (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime

    def resume(self):
        class ThreadEntry(ctypes.Structure):
            _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                        ('th32ThreadID', wintypes.DWORD), ('th32OwnerProcessID', wintypes.DWORD),
                        ('tpBasePri', wintypes.LONG), ('tpDeltaPri', wintypes.LONG), ('dwFlags', wintypes.DWORD)]

        before = self._identity()
        snapshot = self.kernel.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
        if before is None or snapshot in (None, ctypes.c_void_p(-1).value):
            if snapshot not in (None, ctypes.c_void_p(-1).value):
                self.kernel.CloseHandle(snapshot)
            raise OSError("소유한 일시 정지 CLI의 스레드를 확인하지 못했습니다.")
        try:
            entry = ThreadEntry()
            self.kernel.Thread32First.argtypes = self.kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
            entry.dwSize = ctypes.sizeof(entry)
            threads = []
            present = self.kernel.Thread32First(snapshot, ctypes.byref(entry))
            while present:
                if entry.th32OwnerProcessID == self.process.pid:
                    threads.append(entry.th32ThreadID)
                entry.dwSize = ctypes.sizeof(entry)
                present = self.kernel.Thread32Next(snapshot, ctypes.byref(entry))
            if len(threads) != 1:
                raise OSError("일시 정지 CLI의 주 스레드를 확정하지 못했습니다.")
            thread = self.kernel.OpenThread(0x0802, False, threads[0])  # QUERY_LIMITED_INFORMATION | SUSPEND_RESUME
            if not thread:
                raise OSError("소유 CLI 스레드의 핸들을 열지 못했습니다.")
            try:
                if self.kernel.GetProcessIdOfThread(thread) != self.process.pid or self._identity() != before:
                    raise OSError("일시 정지 CLI 또는 스레드 소유자가 바뀌었습니다.")
                if self.kernel.ResumeThread(thread) != 1:
                    raise OSError("소유 CLI 스레드를 재개하지 못했습니다.")
            finally:
                self.kernel.CloseHandle(thread)
        finally:
            self.kernel.CloseHandle(snapshot)

    def terminate(self):
        return bool(self.handle and self.kernel.TerminateJobObject(self.handle, 1))

    def close(self):
        if self.handle:
            # 성공한 CLI가 남긴 백그라운드 자식도 이 실행의 전용 Job 안에서만 정리한다.
            if not self.terminate():
                logger.warning("qa_agent_windows_job_cleanup_failed pid=%s error=%s", self.process.pid, ctypes.get_last_error())
            self.kernel.CloseHandle(self.handle)
            self.handle = None
