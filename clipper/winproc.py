"""Windows: helper programs (ffmpeg recording a live stream, rendering a clip) must stop when Clipper stops.
Windows doesn't do that by itself: a child process outlives its parent and keeps running invisibly (a live
recording would go on filling the disk). Putting Clipper in a job object that kills its members when it closes
fixes that, whether Clipper ends normally, crashes, or its console window is closed."""
from __future__ import annotations

import ctypes
import sys

_job = None  # keep the handle for the life of the process: closing it is what stops the children


def children_die_with_me() -> bool:
    global _job
    if sys.platform != "win32" or _job:
        return bool(_job)

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                    ("SchedulingClass", ctypes.c_uint32)]

    class Io(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("Reads", "Writes", "Others", "ReadBytes", "WriteBytes",
                                                    "OtherBytes")]

    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("Io", Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.windll.kernel32
    k32.CreateJobObjectW.restype = ctypes.c_void_p
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    k32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return False
    info = Extended()
    info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)) and \
            k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
        _job = job
        return True
    return False
