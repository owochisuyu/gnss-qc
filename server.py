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
from qc.diagnosis import (
    slice_analysis_result,
    evaluate_quality_issues,
    detect_sky_obstruction,
    diagnose_station_window,
    parse_timestamp,
    iso_gpst,
)

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


class BatchDiagnosticsRequest(BaseModel):
    job_ids: list[str] | None = None
    start_time: float | str | None = None
    end_time: float | str | None = None


class BatchLoadRequest(BaseModel):
    samples: list[str]
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
        if sum(j["status"] in ("queued","running") for j in jobs.values()) >= 12:
            raise HTTPException(429,"分析队列已满（最多 12 个并发排队任务），请稍后重试。")
        # Keep up to 16 completed analyses in this local process for multi-station comparison.
        finished=[j for j in jobs.values() if j["status"] in ("done","error")]
        for old in sorted(finished,key=lambda j:j["created"])[:-15]:
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


@app.post("/api/batch/load")
def batch_load(request: BatchLoadRequest):
    available = {s["id"]: s for s in samples()}
    created_jobs = []
    settings = options_from(request.options)
    for sid in request.samples:
        if sid in available:
            obs = SAMPLES / f"{sid}_20261028.26o"
            nav = SAMPLES / f"{sid}_61028.nav"
            created_jobs.append(submit(obs, nav, settings, names={"obs": obs.name, "nav": nav.name}))
    return created_jobs


@app.get("/api/batch/timespan")
def batch_timespan(job_ids: str = ""):
    selected_ids = [j.strip() for j in job_ids.split(",") if j.strip()] if job_ids else []
    done_jobs = [j for j in jobs.values() if j["status"] == "done" and (not selected_ids or j["id"] in selected_ids)]
    if not done_jobs:
        return {"has_jobs": False, "stations": 0}

    starts = [j["result"]["series"][0]["time"] for j in done_jobs if j["result"].get("series")]
    ends = [j["result"]["series"][-1]["time"] for j in done_jobs if j["result"].get("series")]
    if not starts or not ends:
        return {"has_jobs": False, "stations": len(done_jobs)}

    earliest_start = min(starts)
    latest_end = max(ends)
    common_start = max(starts)
    common_end = min(ends)
    has_overlap = common_start < common_end

    station_spans = []
    for j in done_jobs:
        s_series = j["result"].get("series", [])
        if s_series:
            st = s_series[0]["time"]
            et = s_series[-1]["time"]
            station_spans.append({
                "job_id": j["id"],
                "station": re.split(r"[_.]", j["label"])[0],
                "start": iso_gpst(st),
                "end": iso_gpst(et),
                "start_time": st,
                "end_time": et,
                "epochs": len(s_series)
            })

    return {
        "has_jobs": True,
        "stations": len(done_jobs),
        "earliest_start": iso_gpst(earliest_start),
        "latest_end": iso_gpst(latest_end),
        "earliest_start_time": earliest_start,
        "latest_end_time": latest_end,
        "common_start": iso_gpst(common_start) if has_overlap else None,
        "common_end": iso_gpst(common_end) if has_overlap else None,
        "common_start_time": common_start if has_overlap else None,
        "common_end_time": common_end if has_overlap else None,
        "has_overlap": has_overlap,
        "overlap_duration": (common_end - common_start) if has_overlap else 0,
        "station_spans": station_spans
    }


@app.post("/api/batch/diagnostics")
def batch_diagnostics(request: BatchDiagnosticsRequest):
    selected_ids = set(request.job_ids) if request.job_ids else None
    done_jobs = [j for j in jobs.values() if j["status"] == "done" and (selected_ids is None or j["id"] in selected_ids)]
    if not done_jobs:
        return {"stations": [], "summary_count": 0}

    results = []
    for j in sorted(done_jobs, key=lambda x: x["label"]):
        diag = diagnose_station_window(j["result"], request.start_time, request.end_time)
        diag["job_id"] = j["id"]
        diag["label"] = j["label"]
        results.append(diag)

    # Compute overall network health
    status_counts = {"PASS": 0, "WARN": 0, "CRITICAL": 0}
    for r in results:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1

    overall_status = "CRITICAL" if status_counts["CRITICAL"] > 0 else "WARN" if status_counts["WARN"] > 0 else "PASS"

    return {
        "stations": results,
        "total": len(results),
        "status_counts": status_counts,
        "overall_status": overall_status,
        "window": {
            "start": iso_gpst(parse_timestamp(request.start_time)) if request.start_time else None,
            "end": iso_gpst(parse_timestamp(request.end_time)) if request.end_time else None,
            "start_time": parse_timestamp(request.start_time),
            "end_time": parse_timestamp(request.end_time)
        }
    }


@app.get("/api/jobs/{identifier}/window")
def station_window(identifier: str, start_time: float | str | None = None, end_time: float | str | None = None):
    job = get_job(identifier, True)
    sliced = slice_analysis_result(job["result"], start_time, end_time)
    diag = evaluate_quality_issues(sliced)
    obstr = detect_sky_obstruction(sliced["satellites"])
    station_name = (
        job.get("result", {}).get("summary", {}).get("station")
        or (re.split(r"[_.]", job["label"])[0] if job.get("label") else "Station")
    )
    return {
        "station": station_name,
        "job_id": identifier,
        "label": job["label"],
        "window": {
            "start": sliced["start"],
            "end": sliced["end"],
            "start_time": sliced["start_time"],
            "end_time": sliced["end_time"],
            "duration": sliced["summary"].get("duration_seconds", 0)
        },
        "status": diag["status"],
        "score": diag["score"],
        "summary": sliced["summary"],
        "by_system": sliced["by_system"],
        "series": sliced["series"],
        "satellites": sliced["satellites"],
        "events": sliced["events"],
        "diagnostics": diag,
        "obstruction": obstr
    }


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
