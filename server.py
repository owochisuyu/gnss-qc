"""Local-only FastAPI service; a single worker protects RTKLIB global state."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
import csv
import io
import json
import logging
import re
import shutil
import time
import uuid

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from qc.analysis import Options, analyze

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache"
CACHE.mkdir(exist_ok=True)
SAMPLES = (ROOT / "samples") if (ROOT / "samples").exists() else (ROOT.parent / "rnx-61-66-1028")
app = FastAPI(title="GNSS Observatory", docs_url=None, redoc_url=None)
pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rtklib")
jobs = {}
guard = Lock()
LIMIT = 256 * 1024 * 1024


@app.middleware("http")
async def local_origin(request: Request, call_next):
    # The app has no external CDN calls and accepts writes only from its own origin.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            return JSONResponse({"detail":"仅允许本地页面提交分析。"},status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


class AnalysisRequest(BaseModel):
    sample: str | None = None
    options: dict = Field(default_factory=dict)


def options_from(data):
    try:
        return Options(**data).validate()
    except (TypeError, ValueError) as exc:
        raise HTTPException(422,str(exc)) from exc


def public_job(job):
    return {k:job[k] for k in ("id","status","progress","message","created","label","error")}


def get_job(identifier, require_result=False):
    job = jobs.get(identifier)
    if job is None:
        raise HTTPException(404,"分析记录已过期，请重新导入。")
    if require_result and job["status"]!="done":
        raise HTTPException(409,"分析尚未完成。")
    return job


def submit(obs, nav, options, owned=None, names=None):
    with guard:
        if sum(j["status"] in ("queued","running") for j in jobs.values()) >= 3:
            raise HTTPException(429,"已有 3 个分析任务在处理，请稍后重试。")
        # Keep up to four completed analyses in this local process.
        finished=[j for j in jobs.values() if j["status"] in ("done","error")]
        for old in sorted(finished,key=lambda j:j["created"])[:-3]:
            jobs.pop(old["id"],None)
            if old["owned"]:
                folder=Path(old["owned"]).resolve()
                if folder.parent == CACHE.resolve():
                    shutil.rmtree(folder,ignore_errors=True)
        identifier=uuid.uuid4().hex
        job={"id":identifier,"status":"queued","progress":0,"message":"等待分析", "created":time.time(),
             "label":(names or {}).get("obs",obs.name),"error":None,"obs":obs,"nav":nav,
             "owned":owned,"names":names,"options":options}
        jobs[identifier]=job
    def run():
        job["status"]="running"
        def progress(percent,message):
            job.update(progress=percent,message=message)
        try:
            result,pairs=analyze(obs,nav,options,progress)
            if names:
                for kind in ("obs","nav"): result["files"][kind]["name"]=names[kind]
            job.update(result=result,pairs=pairs,status="done",progress=100,message="分析完成")
        except Exception as exc:
            logging.exception("Analysis %s failed",identifier)
            job.update(status="error",error=str(exc),message="分析失败")
    pool.submit(run)
    return public_job(job)


@app.get("/api/health")
def health():
    return {"ok":True,"engine":"RTKLIB 2.4.3 b34"}


@app.get("/api/samples")
def samples():
    return [{"id":f"Z{i:03}","obs":f"Z{i:03}_20261028.26o","nav":f"Z{i:03}_61028.nav",
             "size":(SAMPLES/f"Z{i:03}_20261028.26o").stat().st_size}
            for i in range(61,67) if (SAMPLES/f"Z{i:03}_20261028.26o").exists() and (SAMPLES/f"Z{i:03}_61028.nav").exists()]


@app.post("/api/sample")
def sample(request: AnalysisRequest):
    if request.sample not in {s["id"] for s in samples()}:
        raise HTTPException(404,"本地示例不存在。")
    return submit(SAMPLES/f"{request.sample}_20261028.26o",SAMPLES/f"{request.sample}_61028.nav",options_from(request.options))


def safe_name(name, default):
    return re.sub(r"[\\/\x00-\x1f]","_",name or default)[:180]


@app.post("/api/analyze")
async def upload(obs: UploadFile=File(...),nav: UploadFile=File(...),options: str=Form("{}")):
    try:
        settings=options_from(json.loads(options))
    except json.JSONDecodeError as exc:
        raise HTTPException(422,"参数必须是 JSON。") from exc
    folder=CACHE/uuid.uuid4().hex
    folder.mkdir()
    try:
        for field,file in (("obs",obs),("nav",nav)):
            size=0
            with (folder/f"input.{field}").open("wb") as stream:
                while chunk:=await file.read(1024*1024):
                    size+=len(chunk)
                    if size>LIMIT:
                        raise HTTPException(413,"每个文件最大 256 MB。")
                    stream.write(chunk)
            await file.close()
            if not size:
                raise HTTPException(422,f"{field.upper()} 文件为空。")
        return submit(folder/"input.obs",folder/"input.nav",settings,folder,
                      {"obs":safe_name(obs.filename,"input.obs"),"nav":safe_name(nav.filename,"input.nav")})
    except Exception:
        shutil.rmtree(folder,ignore_errors=True)
        raise


@app.get("/api/jobs")
def list_jobs():
    return [public_job(j) for j in sorted(jobs.values(),key=lambda j:j["created"],reverse=True)]


@app.get("/api/jobs/{identifier}")
def job_status(identifier: str):
    return public_job(get_job(identifier))


@app.get("/api/jobs/{identifier}/result")
def result(identifier: str):
    return get_job(identifier,True)["result"]


@app.get("/api/jobs/{identifier}/pair")
def pair(identifier: str,key: str):
    pairs=get_job(identifier,True)["pairs"]
    if key not in pairs:
        raise HTTPException(404,"该卫星没有所选双频组合。")
    return pairs[key]


@app.post("/api/jobs/{identifier}/reanalyze")
def reanalyze(identifier: str,request: AnalysisRequest):
    previous=get_job(identifier)
    settings=options_from(request.options)
    # Give uploaded reanalyses their own files so history eviction is safe.
    folder=None; obs=previous["obs"]; nav=previous["nav"]
    if previous["owned"]:
        folder=CACHE/uuid.uuid4().hex; folder.mkdir()
        shutil.copyfile(obs,folder/"input.obs"); shutil.copyfile(nav,folder/"input.nav")
        obs=folder/"input.obs"; nav=folder/"input.nav"
    try:
        return submit(obs,nav,settings,folder,previous["names"])
    except Exception:
        if folder: shutil.rmtree(folder,ignore_errors=True)
        raise


@app.get("/api/jobs/{identifier}/export/{kind}")
def export(identifier: str,kind: str,key: str=""):
    job=get_job(identifier,True); r=job["result"]
    if kind=="json":
        body=json.dumps({**r,"pair_details":job["pairs"]},ensure_ascii=False,allow_nan=False).encode("utf-8")
        mime="application/json"; ext="json"
    else:
        output=io.StringIO(newline=""); writer=csv.writer(output)
        if kind=="epochs":
            cols=["time","satellites","records","used","pdop","gdop","hdop","vdop","snr","snr_n"]
            writer.writerow(["time_GPST"]+cols[1:])
            from qc.analysis import iso
            for row in r["series"]: writer.writerow([iso(row["time"])]+[row[k] for k in cols[1:]])
        elif kind=="events":
            writer.writerow(["time_GPST","satellite","detectors","max_abs_delta_GF_m","max_abs_delta_MW_m","LLI_fields","pair_details"])
            for ev in r["events"]:
                writer.writerow([ev["time"],ev["sat"],"+".join(ev["detectors"]),ev["dgf"],ev["dmw"],json.dumps(ev["signals"]),json.dumps(ev["pairs"])])
        elif kind=="pair" and key in job["pairs"]:
            data=job["pairs"][key]; writer.writerow(["time_GPST"]+data["columns"])
            from qc.analysis import iso
            for row in data["rows"]: writer.writerow([iso(r["series"][row[0]]["time"])]+row)
        else:
            raise HTTPException(404,"未知导出格式。")
        body=output.getvalue().encode("utf-8-sig"); mime="text/csv"; ext="csv"
    return Response(body,media_type=mime,headers={"Content-Disposition":f'attachment; filename="gnss-qc-{kind}.{ext}"'})


app.mount("/assets",StaticFiles(directory=ROOT/"web"),name="assets")


@app.get("/")
def index():
    return FileResponse(ROOT/"web"/"index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app,host="127.0.0.1",port=8765,log_level="info")
