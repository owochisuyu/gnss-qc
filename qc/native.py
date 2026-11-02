"""Type-safe ctypes facade; all coordinates, frequencies and DOP come from C."""
import ctypes as ct
from pathlib import Path
import os
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DP = ct.POINTER(ct.c_double)
IP = ct.POINTER(ct.c_int)


def doubles(a):
    return a.ctypes.data_as(DP)


class Engine:
    def __init__(self, nav_path):
        library = ROOT / "native" / ("rtkqc.dll" if os.name == "nt" else "librtkqc.so")
        if not library.exists():
            raise RuntimeError("RTKLIB 库不存在，请先运行 python build_native.py。")
        self.lib = ct.CDLL(str(library))
        signatures = {
            "qc_open": ([ct.c_char_p, IP], ct.c_void_p), "qc_close": ([ct.c_void_p], None),
            "qc_sat": ([ct.c_char_p], ct.c_int),
            "qc_freq": ([ct.c_void_p, ct.c_char_p, ct.c_char_p, ct.c_int], ct.c_double),
            "qc_gpst": ([ct.c_double, ct.c_int], ct.c_double),
            "qc_geometry": ([ct.c_void_p, ct.c_double, ct.c_int, IP, DP, DP], None),
            "qc_dops": ([ct.c_int, DP, ct.c_double, DP], None),
            "qc_pos": ([DP, DP], None),
            "qc_spp": ([ct.c_void_p,ct.c_double,ct.c_int,IP,DP,ct.c_char_p,DP,ct.c_char_p],ct.c_int),
        }
        for name, (args, rest) in signatures.items():
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = args, rest
        counts = (ct.c_int*3)()
        # On Windows RTKLIB uses narrow fopen: use the Windows ANSI code page.
        path = str(Path(nav_path).resolve()).encode("mbcs" if os.name == "nt" else "utf-8")
        self.handle = self.lib.qc_open(path, counts)
        if not self.handle:
            raise ValueError("RTKLIB 未读取到有效广播星历，请检查 NAV 格式与内容。")
        self.counts = list(counts)
        self.ids = {}
        self.frequencies = {}

    def close(self):
        if self.handle:
            self.lib.qc_close(self.handle)
            self.handle = None

    def sat(self, sat):
        if sat not in self.ids:
            self.ids[sat] = self.lib.qc_sat(sat.encode())
        return self.ids[sat]

    def frequency(self, sat, code, channels):
        key = sat, code
        if key not in self.frequencies:
            self.frequencies[key] = self.lib.qc_freq(self.handle, sat.encode(), code.encode(), channels.get(sat,99))
        return self.frequencies[key]

    def gpst(self, timestamp, system):
        return self.lib.qc_gpst(timestamp, 1 if system in ("UTC","GLO") else 2 if system=="BDT" else 0)

    def geometry(self, timestamp, satellites, position):
        ids = np.array([self.sat(s) for s in satellites], dtype=np.int32)
        rr = np.array(position, dtype=np.float64)
        result = np.zeros((len(ids),6))
        self.lib.qc_geometry(self.handle,timestamp,len(ids),ids.ctypes.data_as(IP),doubles(rr),doubles(result))
        return result

    def dop(self, azel, mask):
        array = np.ascontiguousarray(azel,dtype=np.float64).reshape(-1,2)
        result = np.zeros(4)
        self.lib.qc_dops(len(array),doubles(array),np.deg2rad(mask),doubles(result))
        return [float(x) if x>0 else None for x in result]

    def geodetic(self, position):
        rr, out = np.array(position,dtype=np.float64), np.zeros(3)
        self.lib.qc_pos(doubles(rr),doubles(out))
        return [float(np.rad2deg(out[0])),float(np.rad2deg(out[1])),float(out[2])]

    def spp(self, epoch):
        rows=[]
        for sat,signals in epoch.satellites.items():
            preferred = "2" if sat[0]=="C" else "1"
            choices=[(code,v[0]) for code,v in signals.items() if code.startswith(preferred) and v[0]]
            if choices:
                rows.append((sat,*choices[0]))
        if len(rows)<4:
            return None
        ids=np.array([self.sat(r[0]) for r in rows],dtype=np.int32)
        ranges=np.array([r[2] for r in rows],dtype=np.float64)
        codes=b"".join(r[1].encode()+b"\0" for r in rows)
        rr, message=np.zeros(3),ct.create_string_buffer(256)
        ok=self.lib.qc_spp(self.handle,epoch.time,len(rows),ids.ctypes.data_as(IP),doubles(ranges),codes,doubles(rr),message)
        return rr.tolist() if ok else None
