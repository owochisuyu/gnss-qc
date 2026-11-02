"""Observable QC, with explicit denominators and no quality-score heuristics."""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from itertools import groupby
from pathlib import Path
import hashlib
import json
import math
import time
import numpy as np

from .native import Engine, ROOT
from .rinex import read_observation, validate_nav, valid

C = 299792458.0
SYSTEMS = {"G":"GPS","R":"GLONASS","E":"Galileo","C":"BeiDou","J":"QZSS","I":"NavIC","S":"SBAS"}


@dataclass
class Options:
    elevation: float = 10.0
    gf_threshold: float = 0.05
    mw_threshold: float = 10.0
    gap_factor: float = 1.5
    min_arc: int = 5
    systems: str = "GRECJIS"
    position: list | None = None

    def validate(self):
        for key,lo,hi in [("elevation",0,89),("gf_threshold",0.001,100),("mw_threshold",0.01,1000),("gap_factor",1.01,100)]:
            value=getattr(self,key)
            if not isinstance(value,(float,int)) or not math.isfinite(value) or not lo<=value<=hi:
                raise ValueError(f"{key} 必须在 {lo}–{hi} 范围内。")
        if not isinstance(self.min_arc,int) or not 2<=self.min_arc<=10000:
            raise ValueError("最短多路径弧段必须为 2–10000 个历元。")
        if not self.systems or set(self.systems)-set(SYSTEMS):
            raise ValueError("至少选择一个有效 GNSS 系统。")
        if self.position is not None and not good_position(self.position):
            raise ValueError("接收机 ECEF XYZ 必须是地球表面附近的三维米制坐标。")
        return self


def good_position(position):
    return (isinstance(position,(list,tuple)) and len(position)==3 and
            all(isinstance(x,(float,int)) and math.isfinite(x) for x in position) and
            6e6 < math.sqrt(sum(x*x for x in position)) < 7e6)


def iso(t):
    # Time has already been normalized to GPST, UTC here is a calendar formatter only.
    return datetime.fromtimestamp(t,timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","")


def stats(values):
    a=np.array([v for v in values if v is not None and math.isfinite(v)],dtype=float)
    if not len(a):
        return {"n":0,"mean":None,"rms":None,"min":None,"max":None,"p95":None}
    return {"n":len(a),"mean":float(np.mean(a)),"rms":float(np.sqrt(np.mean(a*a))),
            "min":float(np.min(a)),"max":float(np.max(a)),"p95":float(np.percentile(a,95))}


def combination(v1,v2,f1,f2):
    """RTKLIB ppp.c gfmeas/mwmeas and RTKPLOT UpdateMp, all in metres."""
    p1,l1,_,_,_=v1; p2,l2,_,_,_=v2
    if not l1 or not l2 or not f1 or not f2 or f1==f2:
        return None
    phase1,phase2=C*l1/f1,C*l2/f2
    gf=phase1-phase2
    mw=(l1-l2)*C/(f1-f2)-(f1*p1+f2*p2)/(f1+f2) if p1 and p2 else None
    alpha=(f1/f2)**2
    mp1=p1-phase1-2*gf/(alpha-1) if p1 else None
    mp2=p2-phase2-2*alpha*gf/(alpha-1) if p2 else None
    return gf,mw,mp1,mp2


def center_arcs(rows, min_arc):
    """Rows: epoch, GF, dGF, MW, dMW, MP1, MP2, bit flags, arc id.

    Missing pseudorange also splits the corresponding MP arc. Short arcs are
    null (never zero); the number of discarded samples is returned explicitly.
    """
    rejected=0
    for col in (5,6):
        segment=[]
        previous=None
        def finish():
            nonlocal rejected
            if len(segment)<min_arc:
                rejected+=len(segment)
                for row in segment: row[col]=None
            elif segment:
                mean=sum(row[col] for row in segment)/len(segment)
                for row in segment: row[col]-=mean
            segment.clear()
        for row in rows:
            if previous is not None and (row[8]!=previous[8] or row[0]!=previous[0]+1):
                finish()
            if row[col] is None:
                finish()
            else:
                segment.append(row)
            previous=row
        finish()
    return rejected


def _segments(indices, epochs, interval, factor):
    if not indices: return []
    result=[]; start=last=indices[0]
    for i in indices[1:]:
        if i!=last+1 or epochs[i].time-epochs[last].time>interval*factor:
            result.append([start,last]); start=i
        last=i
    result.append([start,last])
    return result


def analyze(obs_path,nav_path,options=None,progress=lambda p,m:None):
    started=time.perf_counter()
    options=(options or Options()).validate()
    progress(3,"读取 RINEX 观测文件")
    observation=read_observation(Path(obs_path))
    validate_nav(Path(nav_path))
    engine=Engine(nav_path)
    try:
        return _analyze(observation,engine,Path(obs_path),Path(nav_path),options,progress,started)
    finally:
        engine.close()


def _analyze(observation,engine,obs_path,nav_path,opt,progress,started):
    header,epochs=observation.header,observation.epochs
    warnings=observation.warnings[:]
    for epoch in epochs:
        epoch.time=engine.gpst(epoch.time,header["time_system"])
    interval=header["interval"]
    gap_limit=interval*opt.gap_factor if interval else 1
    position=opt.position or header["position"]
    position_source="手动 ECEF" if opt.position else "RINEX 头部 APPROX POSITION XYZ"
    if not good_position(position):
        position=None
        for epoch in epochs[:100]:
            position=engine.spp(epoch)
            if position is not None: break
        position_source="RTKLIB 单点定位（首个成功历元；固定站址）"
        if position is None:
            warnings.append("接收机坐标缺失且 RTKLIB SPP 未成功；PDOP、高度角与天空图不可用。请填写 ECEF XYZ。")
            position_source="不可用"
    progress(14,"计算广播星历与逐历元几何")
    signal_counts=defaultdict(Counter)
    by_sat=defaultdict(list)
    by_signal=defaultdict(lambda: {"present":0,"phase":0,"code":0,"snr":[],"lli_valid":0,"lli_blank":0,"half_cycle":0})
    events={}
    series=[]
    missing_geometry=unhealthy_geometry=blank_records=phase_total=lli_valid=lli_blank=half_cycle=0
    sat_epochs=0
    snr_series=defaultdict(list)
    def event(index,sat):
        return events.setdefault((index,sat),{"epoch":index,"sat":sat,"detectors":[],"signals":[],"pairs":[],"dgf":None,"dmw":None})
    for index,epoch in enumerate(epochs):
        selected={s:values for s,values in epoch.satellites.items() if s[0] in opt.systems}
        numeric={s:v for s,v in selected.items() if any(any(x is not None for x in a[:4]) for a in v.values())}
        sats=list(numeric)
        geometry=engine.geometry(epoch.time,sats,position) if position else np.tile([0,0,0,0,0,1],(len(sats),1))
        azel=[]; geom_by_system=defaultdict(list); snrs=defaultdict(list); epoch_signals=defaultdict(list)
        for sat,signals in selected.items():
            is_numeric=sat in numeric
            if not is_numeric: blank_records+=1
            for code,values in signals.items():
                p,l,s,_,lli=values
                key=f"{sat[0]}_S{code}"
                st=by_signal[f"{sat[0]}_{code}"]
                st["present"]+=1
                st["phase"]+=int(l is not None); st["code"]+=int(p is not None)
                signal_counts[sat][code]+=int(l is not None)
                if s is not None:
                    st["snr"].append(s); snrs[sat[0]].append(s); epoch_signals[key].append(s)
                if l is not None:
                    phase_total+=1
                    if lli&1: lli_valid+=1; st["lli_valid"]+=1
                    if lli&2: half_cycle+=1; st["half_cycle"]+=1
                    if lli&3:
                        ev=event(index,sat)
                        if "LLI" not in ev["detectors"]: ev["detectors"].append("LLI")
                        ev["signals"].append({"signal":"L"+code,"lli":lli})
                elif lli&1:
                    lli_blank+=1; st["lli_blank"]+=1
        for sat,g in zip(sats,geometry):
            signals=numeric[sat]
            ss=[v[2] for v in signals.values() if v[2] is not None]
            status=int(g[5]); missing_geometry+=status==1; unhealthy_geometry+=status==2
            if status==0 and math.degrees(g[1])>=opt.elevation:
                azel.append(g[:2]); geom_by_system[sat[0]].append(g[:2])
            by_sat[sat].append({"i":index,"signals":signals,
                                "az":float(math.degrees(g[0])) if status!=1 else None,
                                "el":float(math.degrees(g[1])) if status!=1 else None,
                                "geometry":status,"snr":sum(ss)/len(ss) if ss else None})
        all_snr=[s for values in snrs.values() for s in values]
        sat_epochs+=len(sats)
        dop=engine.dop(azel,opt.elevation)
        row={"time":epoch.time,"satellites":len(sats),"records":len(selected),"used":len(azel),
             "gdop":dop[0],"pdop":dop[1],"hdop":dop[2],"vdop":dop[3],
             "snr":sum(all_snr)/len(all_snr) if all_snr else None,"snr_n":len(all_snr),
             "by_system":{s:{"satellites":sum(sat.startswith(s) for sat in sats),
                              "snr":sum(snrs[s])/len(snrs[s]) if snrs[s] else None,
                              "snr_n":len(snrs[s]),"pdop":engine.dop(geom_by_system[s],opt.elevation)[1],
                              "used":len(geom_by_system[s])} for s in opt.systems}}
        series.append(row)
        for key,values in epoch_signals.items():
            snr_series[key].append([index,sum(values)/len(values),len(values)])
        if index%150==0:
            progress(14+int(42*index/len(epochs)),f"星历与几何 {index+1:,} / {len(epochs):,} 历元")
    if not by_sat:
        raise ValueError("所选系统没有有效数值观测。")
    progress(58,"检测 GF / MW / LLI，按连续弧段计算多路径")
    satellites=[]; pair_details={}; gf_comparisons=mw_comparisons=short_mp_samples=0
    all_mp1=[]; all_mp2=[]; skipped_frequencies=set()
    for sat,records in sorted(by_sat.items()):
        # Stable signal choice per frequency: strongest phase coverage, then lexical code.
        bands={}
        for code,n in sorted(signal_counts[sat].items(),key=lambda kv:(-kv[1],kv[0])):
            if n: bands.setdefault(code[0],code)
        order={"G":"1256","R":"12346","E":"15768","C":"261578","J":"1256","I":"59","S":"15"}[sat[0]]
        ordered=sorted(bands.values(),key=lambda c:(order.find(c[0]) if c[0] in order else 99,c))
        pairs=[]
        for code2 in ordered[1:]:
            code1=ordered[0]
            f1=engine.frequency(sat,code1,header["glo_channels"])
            f2=engine.frequency(sat,code2,header["glo_channels"])
            if not f1 or not f2 or f1==f2:
                skipped_frequencies.add(f"{sat}:{code1}/{code2}"); continue
            name=f"L{code1} / L{code2}"; key=f"{sat}:{code1}-{code2}"
            rows=[]; previous=None; arc=0; pair_gf=pair_mw=0; counts=Counter()
            for record in records:
                i=record["i"]; values=record["signals"]
                v1,v2=values.get(code1),values.get(code2)
                lc=combination(v1,v2,f1,f2) if v1 and v2 else None
                if lc is None:
                    previous=None; continue
                gf,mw,mp1,mp2=lc
                connected=(previous is not None and i==previous[0]+1 and
                           epochs[i].time-epochs[previous[0]].time<=gap_limit and
                           not epochs[i].flag and not epochs[previous[0]].flag)
                dgf=gf-previous[1] if connected else None
                dmw=mw-previous[3] if connected and mw is not None and previous[3] is not None else None
                flags=(1 if dgf is not None and abs(dgf)>opt.gf_threshold else 0) | \
                      (2 if dmw is not None and abs(dmw)>opt.mw_threshold else 0) | \
                      (4 if (v1[4]|v2[4])&3 else 0)
                pair_gf+=dgf is not None; pair_mw+=dmw is not None
                if not connected or flags: arc+=1
                row=[i,gf,dgf,mw,dmw,mp1,mp2,flags,arc]; rows.append(row); previous=row
                if flags:
                    ev=event(i,sat)
                    ev["pairs"].append({"pair":name,"gf":bool(flags&1),"mw":bool(flags&2),"dgf":dgf,"dmw":dmw})
                    for bit,detector in [(1,"GF"),(2,"MW"),(4,"LLI")]:
                        if flags&bit:
                            counts[detector]+=1
                            if detector not in ev["detectors"]: ev["detectors"].append(detector)
                    if dgf is not None and (ev["dgf"] is None or abs(dgf)>abs(ev["dgf"])): ev["dgf"]=dgf
                    if dmw is not None and (ev["dmw"] is None or abs(dmw)>abs(ev["dmw"])): ev["dmw"]=dmw
            short=center_arcs(rows,opt.min_arc); short_mp_samples+=short
            mp1s=[r[5] for r in rows if r[5] is not None]; mp2s=[r[6] for r in rows if r[6] is not None]
            all_mp1.extend(mp1s); all_mp2.extend(mp2s)
            pair_gf=int(pair_gf); pair_mw=int(pair_mw)
            gf_comparisons+=pair_gf; mw_comparisons+=pair_mw
            meta={"key":key,"name":name,"f1":f1,"f2":f2,"n":len(rows),"gf_comparisons":pair_gf,
                  "mw_comparisons":pair_mw,"detectors":dict(counts),"mp1":stats(mp1s),"mp2":stats(mp2s),
                  "short_mp_samples":short,"arcs":arc}
            pairs.append(meta)
            pair_details[key]={**meta,"columns":["epoch","gf_m","delta_gf_m","mw_m","delta_mw_m","mp1_m","mp2_m","flags","arc"],"rows":rows}
        sat_events=[ev for (i,s),ev in events.items() if s==sat]
        satellites.append({"id":sat,"system":sat[0],"epochs":len(records),"coverage":len(records)/len(epochs),
                           "segments":_segments([r["i"] for r in records],epochs,interval or 1,opt.gap_factor),
                           "snr":stats([r["snr"] for r in records]),"events":len(sat_events),
                           "pairs":pairs,"track":[[r["i"],r["az"],r["el"],r["snr"],r["geometry"]] for r in records]})
    events_list=sorted(events.values(),key=lambda e:(e["epoch"],e["sat"]))
    detector_counts=Counter(d for event in events_list for d in event["detectors"])
    for event in events_list:
        event["time"]=iso(epochs[event["epoch"]].time)
    gaps=[{"before":iso(a.time),"after":iso(b.time),"duration":b.time-a.time,
           "missing_epochs":max(0,round((b.time-a.time)/interval)-1) if interval else None}
          for a,b in zip(epochs,epochs[1:]) if interval and b.time-a.time>gap_limit]
    if missing_geometry:
        warnings.append(f"{missing_geometry:,} 条有效卫星历元缺少可用星历或站址，已从几何指标排除；观测统计保留。")
    if unhealthy_geometry:
        warnings.append(f"{unhealthy_geometry:,} 条卫星历元星历健康标志非零，已从 PDOP 排除。")
    if lli_blank:
        warnings.append(f"{lli_blank:,} 个空相位字段带 LLI bit0，单列计数，未加入有效相位周跳候选事件。")
    if skipped_frequencies:
        warnings.append(f"{len(skipped_frequencies)} 个信号组合无法确定不同载频，未计算 GF/MW/MP。")
    if not all_mp1:
        warnings.append("没有满足最短弧长的 MP1 样本；请检查双频载波、周跳密度及弧长设置。")
    expected=round((epochs[-1].time-epochs[0].time)/interval)+1 if interval else len(epochs)
    all_snr=[v for st in by_signal.values() for v in st["snr"]]
    summary={"epochs":len(epochs),"valid_epochs":sum(r["satellites"]>0 for r in series),
             "expected_epochs":expected,"epoch_completeness":min(1,len(epochs)/expected),
             "satellites":len(satellites),"satellite_epochs":sat_epochs,"blank_satellite_records":blank_records,
             "mean_satellites":sat_epochs/len(epochs),"phase_observations":phase_total,
             "lli_phase_fields":lli_valid,"lli_blank_fields":lli_blank,"half_cycle_fields":half_cycle,
             "events":len(events_list),"event_rate_per_1000_satellite_epochs":len(events_list)/sat_epochs*1000,
             "detectors":{d:detector_counts[d] for d in ("GF","MW","LLI")},
             "gf_comparisons":gf_comparisons,"mw_comparisons":mw_comparisons,
             "pdop":stats([r["pdop"] for r in series]),"cn0":stats(all_snr),
             "mp1":stats(all_mp1),"mp2":stats(all_mp2),"short_mp_samples":short_mp_samples,
             "missing_geometry":missing_geometry,"unhealthy_geometry":unhealthy_geometry,"gaps":gaps}
    source=json.loads((ROOT/"vendor"/"rtklib"/"SOURCE.json").read_text())
    result={"schema_version":1,"engine":"RTKLIB 2.4.3 b34","engine_commit":source["commit"],
            "files":{"obs":{"name":obs_path.name,"bytes":obs_path.stat().st_size,"sha256":hashlib.sha256(obs_path.read_bytes()).hexdigest()},
                     "nav":{"name":nav_path.name,"bytes":nav_path.stat().st_size,"sha256":hashlib.sha256(nav_path.read_bytes()).hexdigest()}},
            "header":header,"time_system":"GPST","start":iso(epochs[0].time),"end":iso(epochs[-1].time),
            "interval":interval,"options":asdict(opt),"position":{"ecef":position,"geodetic":engine.geodetic(position) if position else None,"source":position_source},
            "navigation":{"kepler":engine.counts[0],"glonass":engine.counts[1],"sbas":engine.counts[2]},
            "summary":summary,"series":series,"satellites":satellites,"events":events_list,
            "signals":[{"id":key,**{k:v for k,v in st.items() if k!="snr"},"snr":stats(st["snr"])} for key,st in sorted(by_signal.items())],
            "snr_series":dict(snr_series),"warnings":warnings,"elapsed_seconds":time.perf_counter()-started}
    progress(98,"整理统计与图表")
    return result,pair_details
