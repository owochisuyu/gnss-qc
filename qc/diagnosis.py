"""GNSS Station Quality Diagnostics & Time-Window Analysis Engine.

Provides quantitative assessment of GNSS observation quality across:
1. Physical sky obstruction (azimuth-elevation coverage & cut-off detection)
2. Multi-system SNR / C/N0 evaluation (GPS, GLONASS, Galileo, BeiDou)
3. Satellite availability and PDOP spatial geometry degradation
4. Cycle slip frequency and loss-of-lock tracking continuity
5. Observation data completeness & gap analysis
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
import numpy as np


def parse_timestamp(val):
    """Normalize timestamp float or ISO string to float GPST seconds."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        # Format: 2026-10-28T07:54:30.000 or 2026-10-28 07:54:30
        clean = val.replace("Z", "").replace(" ", "T")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    raise ValueError(f"无法识别的时间戳格式: {val}")


def iso_gpst(t):
    """Format GPST timestamp to ISO-like UTC calendar representation."""
    if t is None:
        return None
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "")


def slice_analysis_result(result, t_start=None, t_end=None):
    """Slice a full analysis result dict into a sub-window [t_start, t_end].

    Returns a trimmed result structure with re-evaluated time series,
    satellite tracks, events, and sub-window summary statistics.
    """
    series_all = result.get("series", [])
    if not series_all:
        raise ValueError("分析结果中没有历元时序数据。")

    all_times = [r["time"] for r in series_all]
    min_t, max_t = min(all_times), max(all_times)

    ts = parse_timestamp(t_start) if t_start is not None else min_t
    te = parse_timestamp(t_end) if t_end is not None else max_t

    if ts > te:
        ts, te = te, ts

    # Filter epoch series
    sliced_series = [r for r in series_all if ts <= r["time"] <= te]
    if not sliced_series:
        # Return empty shell with zero counts
        return {
            "start": iso_gpst(ts), "end": iso_gpst(te),
            "start_time": ts, "end_time": te,
            "epochs": 0, "valid_epochs": 0, "expected_epochs": 0, "completeness": 0.0,
            "series": [], "satellites": [], "events": [],
            "by_system": {}, "summary": {}, "empty": True
        }

    valid_indices = {r["time"]: idx for idx, r in enumerate(series_all) if ts <= r["time"] <= te}
    time_set = set(valid_indices.keys())

    interval = result.get("interval") or 30.0
    expected = max(1, round((te - ts) / interval) + 1)
    actual_epochs = len(sliced_series)
    completeness = min(1.0, actual_epochs / expected)

    # Filter events
    sliced_events = [ev for ev in result.get("events", []) if ev.get("time") and ts <= parse_timestamp(ev["time"]) <= te]

    # Filter satellites and their trajectory tracks
    sliced_satellites = []
    systems_present = set()
    total_sat_epochs = 0

    for sat in result.get("satellites", []):
        sat_id = sat["id"]
        sys_key = sat.get("system") or sat_id[0]
        # track item: [epoch_idx, az, el, snr, geom_status]
        tracks_in_win = []
        for tr in sat.get("track", []):
            ep_idx = tr[0]
            if 0 <= ep_idx < len(series_all):
                t_ep = series_all[ep_idx]["time"]
                if t_ep in time_set:
                    tracks_in_win.append(tr)

        if tracks_in_win:
            systems_present.add(sys_key)
            total_sat_epochs += len(tracks_in_win)
            snrs = [tr[3] for tr in tracks_in_win if tr[3] is not None]
            sat_evs = [ev for ev in sliced_events if ev.get("sat") == sat_id]
            sliced_satellites.append({
                "id": sat_id,
                "system": sys_key,
                "epochs": len(tracks_in_win),
                "track": tracks_in_win,
                "snr": {
                    "mean": float(np.mean(snrs)) if snrs else None,
                    "min": float(np.min(snrs)) if snrs else None,
                    "max": float(np.max(snrs)) if snrs else None,
                    "n": len(snrs)
                },
                "events": len(sat_evs)
            })

    # Summary by system (GPS, GLONASS, Galileo, BeiDou, etc.)
    by_system = {}
    for sys_code in sorted(systems_present):
        sys_snrs = []
        sys_sats_per_epoch = []
        sys_pdops = []
        sys_used_per_epoch = []

        for row in sliced_series:
            sys_info = row.get("by_system", {}).get(sys_code, {})
            if sys_info:
                if sys_info.get("snr") is not None:
                    sys_snrs.append(sys_info["snr"])
                if sys_info.get("satellites") is not None:
                    sys_sats_per_epoch.append(sys_info["satellites"])
                if sys_info.get("used") is not None:
                    sys_used_per_epoch.append(sys_info["used"])
                if sys_info.get("pdop") is not None and math.isfinite(sys_info["pdop"]):
                    sys_pdops.append(sys_info["pdop"])

        by_system[sys_code] = {
            "satellites_mean": float(np.mean(sys_sats_per_epoch)) if sys_sats_per_epoch else 0.0,
            "satellites_min": int(np.min(sys_sats_per_epoch)) if sys_sats_per_epoch else 0,
            "used_mean": float(np.mean(sys_used_per_epoch)) if sys_used_per_epoch else 0.0,
            "used_min": int(np.min(sys_used_per_epoch)) if sys_used_per_epoch else 0,
            "snr_mean": float(np.mean(sys_snrs)) if sys_snrs else None,
            "snr_min": float(np.min(sys_snrs)) if sys_snrs else None,
            "pdop_mean": float(np.mean(sys_pdops)) if sys_pdops else None,
            "pdop_max": float(np.max(sys_pdops)) if sys_pdops else None,
            "pdop_n": len(sys_pdops)
        }

    # Overall PDOP stats
    pdops = [r["pdop"] for r in sliced_series if r.get("pdop") is not None and math.isfinite(r["pdop"])]
    all_snrs = [r["snr"] for r in sliced_series if r.get("snr") is not None and math.isfinite(r["snr"])]
    used_counts = [r.get("used", 0) for r in sliced_series]
    sat_counts = [r.get("satellites", 0) for r in sliced_series]

    summary = {
        "start": iso_gpst(ts),
        "end": iso_gpst(te),
        "start_time": ts,
        "end_time": te,
        "duration_seconds": te - ts,
        "epochs": actual_epochs,
        "expected_epochs": expected,
        "completeness": completeness,
        "valid_epochs": sum(1 for r in sliced_series if r.get("satellites", 0) > 0),
        "total_sat_epochs": total_sat_epochs,
        "mean_satellites": float(np.mean(sat_counts)) if sat_counts else 0.0,
        "min_satellites": int(np.min(sat_counts)) if sat_counts else 0,
        "mean_used": float(np.mean(used_counts)) if used_counts else 0.0,
        "min_used": int(np.min(used_counts)) if used_counts else 0,
        "pdop": {
            "mean": float(np.mean(pdops)) if pdops else None,
            "max": float(np.max(pdops)) if pdops else None,
            "min": float(np.min(pdops)) if pdops else None,
            "p95": float(np.percentile(pdops, 95)) if pdops else None,
            "poor_ratio": sum(1 for p in pdops if p > 3.0) / len(pdops) if pdops else 0.0,
            "critical_ratio": sum(1 for p in pdops if p > 5.0) / len(pdops) if pdops else 0.0,
            "valid_epochs": len(pdops)
        },
        "snr": {
            "mean": float(np.mean(all_snrs)) if all_snrs else None,
            "min": float(np.min(all_snrs)) if all_snrs else None,
            "max": float(np.max(all_snrs)) if all_snrs else None
        },
        "events": {
            "count": len(sliced_events),
            "rate_per_1000_sat_epochs": (len(sliced_events) / total_sat_epochs * 1000.0) if total_sat_epochs > 0 else 0.0
        }
    }

    return {
        "start": iso_gpst(ts),
        "end": iso_gpst(te),
        "start_time": ts,
        "end_time": te,
        "series": sliced_series,
        "satellites": sliced_satellites,
        "events": sliced_events,
        "by_system": by_system,
        "summary": summary,
        "empty": False
    }


def detect_sky_obstruction(satellites, min_elevation_cutoff=10.0, sector_deg=30):
    """Analyze satellite tracking points to detect directional physical obstructions.

    Divides azimuth 0°-360° into 12 sectors (30° each).
    In mid-latitude northern hemisphere, azimuth 330°-30° has a natural constellation
    geometry void (Northern Void) for non-polar orbits (GPS 55°, Galileo 56°);
    we distinguish this natural geometric gap from physical obstructions.

    A physical obstruction is diagnosed when a sector has active satellite passes
    (multiple satellites or ample points) but signals consistently terminate at an
    abnormally high elevation (>= 22°) instead of descending towards the cutoff angle.
    """
    num_sectors = 360 // sector_deg
    sector_points = [[] for _ in range(num_sectors)]
    sector_sats = [set() for _ in range(num_sectors)]

    for sat in satellites:
        sat_id = sat["id"]
        for tr in sat.get("track", []):
            az, el, status = tr[1], tr[2], tr[4]
            if az is not None and el is not None and status == 0:
                sec_idx = int((az % 360) // sector_deg)
                sector_points[sec_idx].append(el)
                sector_sats[sec_idx].add(sat_id)

    total_pts = sum(len(p) for p in sector_points)
    if total_pts < 80:
        return {
            "has_obstruction": False,
            "sectors": [],
            "description": "时段较短或可用星历点不足（< 80 点），无法可靠分离物理遮挡与正常无星间隙。"
        }

    sectors = []
    for i in range(num_sectors):
        az_start = i * sector_deg
        az_end = (i + 1) * sector_deg
        pts = sector_points[i]
        sats_in_sec = sector_sats[i]

        # Check if this sector lies in the northern sky void (approx 330°-30°)
        is_northern_void = (az_start >= 330 or az_end <= 30)

        # Requires at least 15 points and at least 2 satellites to infer a physical wall
        if len(pts) >= 15 and len(sats_in_sec) >= 2:
            min_el = float(np.min(pts))
            # If lowest tracking elevation is significantly elevated (>= 24°)
            if min_el >= 24.0:
                # If in northern void, note it as geometric void rather than critical wall unless extremely high (> 45°)
                if is_northern_void and min_el < 40.0:
                    continue
                severity = "CRITICAL" if min_el >= 35.0 else "WARN"
                sectors.append({
                    "az_start": az_start,
                    "az_end": az_end,
                    "cutoff_elevation": round(min_el, 1),
                    "satellite_count": len(sats_in_sec),
                    "points": len(pts),
                    "severity": severity,
                    "label": f"{az_start}°–{az_end}° (低空截断 ~{round(min_el, 0):.0f}°)"
                })

    has_obstr = len(sectors) > 0
    if has_obstr:
        desc_list = [s["label"] for s in sectors]
        description = f"在方位角 {', '.join(desc_list)} 出现持续的低仰角信号截断，疑似存在局部实体建筑或地形物理遮挡。"
    else:
        description = "卫星天空视场开阔，未发现持续的定向物理遮挡盲区。"

    return {
        "has_obstruction": has_obstr,
        "sectors": sectors,
        "description": description
    }


def evaluate_quality_issues(sliced_result):
    """Evaluate comprehensive quality health across 5 core dimensions.

    Returns structured status (PASS / WARN / CRITICAL), issue tags,
    numerical scores, and human-readable diagnostic paragraphs.
    """
    if sliced_result.get("empty"):
        return {
            "status": "CRITICAL",
            "score": 0,
            "issues": [{"code": "NO_DATA", "level": "CRITICAL", "message": "该选定时段内无任何有效观测历元。"}],
            "obstruction": {"has_obstruction": False, "sectors": [], "description": "无观测数据。"},
            "summary_text": "【无数据】选定时段内无可用观测数据。"
        }

    summary = sliced_result["summary"]
    by_sys = sliced_result["by_system"]
    satellites = sliced_result["satellites"]

    issues = []
    level_weights = {"PASS": 0, "WARN": 1, "CRITICAL": 3}
    max_severity = "PASS"

    def add_issue(code, level, message):
        nonlocal max_severity
        issues.append({"code": code, "level": level, "message": message})
        if level_weights[level] > level_weights[max_severity]:
            max_severity = level

    # 1. Completeness
    completeness = summary.get("completeness", 0.0)
    if completeness < 0.85:
        add_issue("LOW_COMPLETENESS", "CRITICAL", f"数据完整率严重偏低 ({completeness*100:.1f}%)，存在大面积通信丢失或接收机断流。")
    elif completeness < 0.95:
        add_issue("MODERATE_COMPLETENESS", "WARN", f"数据完整率略低 ({completeness*100:.1f}%)，存在零星历元缺测。")

    # 2. Obstruction
    obstr = detect_sky_obstruction(satellites)
    if obstr["has_obstruction"]:
        any_critical = any(s["severity"] == "CRITICAL" for s in obstr["sectors"])
        add_issue(
            "SKY_OBSTRUCTION",
            "CRITICAL" if any_critical else "WARN",
            obstr["description"]
        )

    # 3. SNR / C/N0 Evaluation
    overall_snr = summary["snr"].get("mean")
    if overall_snr is not None:
        if overall_snr < 36.0:
            add_issue("CRITICAL_LOW_SNR", "CRITICAL", f"全星座平均载噪比严重偏弱 ({overall_snr:.1f} dB-Hz)，可能存在射频链路重度衰减或严重同频干扰。")
        elif overall_snr < 40.0:
            add_issue("LOW_SNR", "WARN", f"全星座平均载噪比偏低 ({overall_snr:.1f} dB-Hz)，天线增益或射频性能一般。")

    sys_names = {"G": "GPS", "R": "GLONASS", "E": "Galileo", "C": "BeiDou"}
    for sys_code, sname in sys_names.items():
        if sys_code in by_sys:
            m_snr = by_sys[sys_code].get("snr_mean")
            if m_snr is not None:
                if m_snr < 35.0:
                    add_issue(f"LOW_SNR_{sname}", "CRITICAL", f"{sname} 系统平均信噪比极低 ({m_snr:.1f} dB-Hz)。")
                elif m_snr < 38.5:
                    add_issue(f"WEAK_SNR_{sname}", "WARN", f"{sname} 系统平均信噪比偏弱 ({m_snr:.1f} dB-Hz)。")

    # 4. Satellite Count & PDOP Geometry
    min_sats = summary.get("min_satellites", 0)
    mean_sats = summary.get("mean_satellites", 0.0)
    min_used = summary.get("min_used", 0)

    if min_used < 4 and summary["epochs"] > 10:
        add_issue("INSUFFICIENT_SATS", "CRITICAL", f"存在可用卫星数不足 4 颗的时段（最低仅 {min_used} 颗），无法正常定位解算。")
    elif min_used < 7:
        add_issue("LOW_SATS", "WARN", f"部分历元几何可用卫星数偏少（最低 {min_used} 颗，均值 {summary.get('mean_used', 0):.1f} 颗）。")

    pdop_info = summary.get("pdop", {})
    pdop_mean = pdop_info.get("mean")
    pdop_max = pdop_info.get("max")
    poor_ratio = pdop_info.get("poor_ratio", 0.0)

    if pdop_mean is not None:
        if pdop_mean > 4.5 or poor_ratio > 0.20:
            add_issue("CRITICAL_PDOP", "CRITICAL", f"PDOP 均值严重劣化 ({pdop_mean:.2f})，空间几何构型极差。")
        elif pdop_mean > 2.8 or poor_ratio > 0.05:
            add_issue("POOR_PDOP", "WARN", f"PDOP 构型偏弱（均值 {pdop_mean:.2f}，峰值 {pdop_max:.2f}，劣质率 {poor_ratio*100:.1f}%）。")

    # 5. Cycle Slip Frequency
    ev_info = summary.get("events", {})
    ev_rate = ev_info.get("rate_per_1000_sat_epochs", 0.0)
    if ev_rate > 20.0:
        add_issue("EXCESSIVE_CYCLE_SLIPS", "CRITICAL", f"周跳发生极其频繁（每千历元 {ev_rate:.1f} 次候选周跳），载波跟踪极不稳定。")
    elif ev_rate > 8.0:
        add_issue("ELEVATED_CYCLE_SLIPS", "WARN", f"周跳率偏高（每千历元 {ev_rate:.1f} 次候选周跳），建议排查多路径反射或低仰角信号。")

    # Generate narrative summary
    time_str = f"{sliced_result['start']} 至 {sliced_result['end']} GPST"
    if max_severity == "PASS":
        summary_text = (
            f"【测站正常 🟢】在 {time_str} 时段内：数据完整率 {completeness*100:.1f}%，"
            f"可用星数均值 {summary.get('mean_used', 0):.1f} 颗，PDOP 均值 {pdop_mean:.2f} 构型优良；"
            f"全星座平均载噪比 {overall_snr:.1f} dB-Hz，周跳发生率低 ({ev_rate:.1f} 次/千星历元)，未发现定向物理遮挡。"
        )
    elif max_severity == "WARN":
        summary_text = f"【需关注 🟡】在 {time_str} 时段内发现关注事项：\n" + "\n".join(f"• {iss['message']}" for iss in issues if iss['level'] != 'PASS')
    else:
        summary_text = f"【异常警报 🔴】在 {time_str} 时段内发现严重质量缺陷：\n" + "\n".join(f"• {iss['message']}" for iss in issues if iss['level'] == 'CRITICAL')

    # Overall score (0 to 100)
    score = 100
    for iss in issues:
        score -= 25 if iss["level"] == "CRITICAL" else 10
    score = max(10, min(100, score))

    return {
        "status": max_severity,
        "score": score,
        "issues": issues,
        "obstruction": obstr,
        "summary_text": summary_text
    }


def diagnose_station_window(result, t_start=None, t_end=None):
    """Entrypoint: slice result to window and evaluate complete diagnostics."""
    sliced = slice_analysis_result(result, t_start, t_end)
    diag = evaluate_quality_issues(sliced)
    station_name = (
        result.get("summary", {}).get("station")
        or re.split(r"[_.]", result.get("files", {}).get("obs", {}).get("name", "Station"))[0]
    )
    return {
        "station": station_name,
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
        "obstruction": diag["obstruction"],
        "issues": diag["issues"],
        "report_text": diag["summary_text"]
    }
