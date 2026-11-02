"""Double-click friendly launcher, reuses an already running local instance."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT=Path(__file__).resolve().parent
URL="http://127.0.0.1:8765"

def ready():
    try:
        with urllib.request.urlopen(URL+"/api/health",timeout=1) as response:
            return b'RTKLIB' in response.read()
    except OSError:
        return False

def main():
    if ready():
        webbrowser.open(URL); return
    missing=[n for n in ("numpy","fastapi","uvicorn","multipart") if not importlib.util.find_spec(n)]
    if missing:
        print("Missing dependencies: "+", ".join(missing))
        print(f'Run: "{sys.executable}" -m pip install -r "{ROOT / "requirements.txt"}"')
        input("Press Enter to close..."); return
    from build_native import LIB, build
    if not LIB.exists(): build()
    (ROOT/"outputs").mkdir(exist_ok=True)
    log=(ROOT/"outputs"/"server.log").open("a",encoding="utf-8")
    flags=subprocess.CREATE_NO_WINDOW if sys.platform=="win32" else 0
    process=subprocess.Popen([sys.executable,str(ROOT/"server.py")],cwd=ROOT,stdout=log,stderr=log,creationflags=flags)
    for _ in range(50):
        if ready(): webbrowser.open(URL); return
        if process.poll() is not None: break
        time.sleep(.2)
    print(f"Server did not start. Check {ROOT/'outputs'/'server.log'}")
    input("Press Enter to close...")

if __name__=="__main__": main()
