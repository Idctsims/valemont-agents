"""The worker's own memory: what job_health notes report, and the release
after a big job (Chat 3 Phase 1 hotfix, owner 2026-10-12: Railway showed
348 MB, up from ~40).

    rss_mb()        resident set size now, in MB (2^20): Linux /proc/self/status
                    VmRSS, Windows GetProcessMemoryInfo; None elsewhere.
    PeakSampler     a context manager that samples rss_mb() on a thread every
                    SAMPLE_S while a job runs: the job's peak. RSS is the whole
                    process, so a peak includes any job running alongside.
    release()       gc.collect(), then glibc malloc_trim(0) where it exists, so
                    freed memory goes back to the OS instead of sitting in the
                    allocator's arenas (on Linux, freed Python memory is often
                    never returned without it). Best effort; never raises.

Nothing here allocates per sample beyond a few objects.
"""

from __future__ import annotations

import ctypes
import gc
import logging
import sys
import threading
from types import TracebackType

log = logging.getLogger("valemont.memory")

MB = 2 ** 20
SAMPLE_S = 0.25


def _rss_linux() -> float | None:
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024 / MB  # kB
    except OSError:
        return None
    return None


class _PMC(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def _rss_windows() -> float | None:
    try:
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        psapi = ctypes.windll.psapi  # type: ignore[attr-defined]
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PMC), ctypes.c_ulong]
        pmc = _PMC()
        pmc.cb = ctypes.sizeof(_PMC)
        if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return None
        return pmc.WorkingSetSize / MB
    except (AttributeError, OSError):
        return None


def rss_mb() -> float | None:
    if sys.platform.startswith("linux"):
        return _rss_linux()
    if sys.platform == "win32":
        return _rss_windows()
    return None


class PeakSampler:
    """`with PeakSampler() as s: ...` then s.peak_mb / s.end_mb (None when
    RSS cannot be read on this platform)."""

    def __init__(self, interval_s: float = SAMPLE_S) -> None:
        self.interval_s = interval_s
        self.peak_mb: float | None = None
        self.end_mb: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _sample(self) -> None:
        now = rss_mb()
        if now is not None and (self.peak_mb is None or now > self.peak_mb):
            self.peak_mb = now

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            self._sample()

    def __enter__(self) -> "PeakSampler":
        self._sample()
        self._thread = threading.Thread(target=self._run, name="rss-sampler", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._sample()
        self.end_mb = rss_mb()

    def note(self) -> str | None:
        """'rss 84 MB, job peak 131 MB', or None when RSS is unknown."""
        if self.end_mb is None or self.peak_mb is None:
            return None
        return f"rss {self.end_mb:.0f} MB, job peak {max(self.peak_mb, self.end_mb):.0f} MB"


_LIBC: object | None = None


def release() -> None:
    """Collect, then hand freed heap back to the OS (glibc only)."""
    global _LIBC
    gc.collect()
    if not sys.platform.startswith("linux"):
        return
    try:
        if _LIBC is None:
            _LIBC = ctypes.CDLL("libc.so.6")
        _LIBC.malloc_trim(0)  # type: ignore[attr-defined]
    except (OSError, AttributeError) as exc:
        log.debug("malloc_trim unavailable: %s", exc)
        _LIBC = False
