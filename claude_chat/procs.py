# -*- coding: utf-8 -*-
"""Windows 行程查詢（ctypes）：pid 活著嗎、行程建立時間（pid 會被回收再用，要配建立時間才認得是同一隻）。"""
import ctypes


def _pid_alive(pid):
    try:
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
    except Exception:
        pass
    return False


def _proc_start_ft(pid):
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)
    if not h:
        return "0"
    import ctypes.wintypes as w

    class FT(ctypes.Structure):
        _fields_ = [("lo", w.DWORD), ("hi", w.DWORD)]
    c, e, kk, u = FT(), FT(), FT(), FT()
    try:
        k.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kk), ctypes.byref(u))
    finally:
        k.CloseHandle(h)
    return str((c.hi << 32) | c.lo)
