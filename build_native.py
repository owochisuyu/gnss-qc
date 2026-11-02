"""Build the local RTKLIB bridge. No network access or package installation."""
from pathlib import Path
import os
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
LIB = ROOT / "native" / ("rtkqc.dll" if os.name == "nt" else "librtkqc.so")

def build():
    compiler = shutil.which("gcc") or shutil.which("cc")
    if not compiler:
        raise RuntimeError("需要 GCC 编译 RTKLIB；Windows 可安装 MinGW-w64 并加入 PATH。")
    src = ROOT / "vendor" / "rtklib"
    names = ["rtkcmn.c", "rinex.c", "ephemeris.c", "preceph.c", "sbas.c", "pntpos.c", "ionex.c"]
    args = [compiler, "-O2", "-std=c11", "-shared", "-DNFREQ=3", "-DNEXOBS=16", "-DMAXOBS=192",
            "-DENAGLO", "-DENAGAL", "-DENAQZS", "-DENACMP", "-DENAIRN", "-I", str(src),
            str(ROOT / "native" / "bridge.c"), *[str(src / name) for name in names], "-o", str(LIB)]
    if os.name == "nt":
        args += ["-DWIN32", "-static", "-lwinmm", "-lws2_32"]
    else:
        args += ["-fPIC", "-lm", "-lpthread"]
    subprocess.run(args, check=True)
    print(f"Built {LIB}")

if __name__ == "__main__":
    build()
