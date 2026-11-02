"""Strict RINEX 3 observation reader preserving blank-phase LLI fields.

RTKLIB intentionally drops empty observation fields. QC needs to retain those
flags separately, hence this reader; orbit, frequency and DOP use RTKLIB itself.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
import math
import re
import statistics


class RinexError(ValueError):
    pass


@dataclass
class Epoch:
    time: float
    flag: int
    # signal: [pseudorange (m), phase (cycles), C/N0 (dB-Hz), Doppler, LLI]
    satellites: dict = field(default_factory=dict)


@dataclass
class Observation:
    header: dict
    epochs: list[Epoch]
    warnings: list[str]


def valid(x):
    return x is not None and math.isfinite(x) and x != 0


def read_observation(path: Path) -> Observation:
    lines = path.read_text(encoding="ascii", errors="strict").splitlines()
    if not lines or "RINEX VERSION / TYPE" not in lines[0]:
        raise RinexError("OBS 缺少 RINEX VERSION / TYPE 文件头。")
    version = float(lines[0][:9])
    if not 3.0 <= version < 4.0 or lines[0][20:21] != "O":
        raise RinexError("当前版本支持 RINEX 3.x OBS；请将 RINEX 2/4 或 CRINEX 转换为 RINEX 3 后导入。")
    header = {"version": version, "types": {}, "position": None, "interval": None,
              "time_system": "GPS", "marker": "", "receiver": "", "antenna": "", "glo_channels": {}}
    expected, current, body = {}, None, None
    warnings = []
    for i, line in enumerate(lines[1:], 1):
        label = line[60:80].strip()
        if label == "SYS / # / OBS TYPES":
            if line[0] != " ":
                current = line[0]
                expected[current] = int(line[3:6])
                header["types"][current] = []
            if current is None:
                raise RinexError(f"第 {i+1} 行观测类型续行缺少系统。")
            header["types"][current].extend(line[7:60].split())
        elif label == "APPROX POSITION XYZ":
            header["position"] = [float(v) for v in line[:60].split()]
        elif label == "INTERVAL":
            header["interval"] = float(line[:10])
        elif label == "MARKER NAME":
            header["marker"] = line[:60].strip()
        elif label == "REC # / TYPE / VERS":
            header["receiver"] = line[:60].strip()
        elif label == "ANT # / TYPE":
            header["antenna"] = line[:60].strip()
        elif label == "TIME OF FIRST OBS":
            header["time_system"] = line[48:51].strip() or "GPS"
        elif label == "GLONASS SLOT / FRQ #":
            for sat, ch in re.findall(r"(R\d{2})\s+(-?\d+)", line[:60]):
                header["glo_channels"][sat] = int(ch)
        elif label == "SYS / SCALE FACTOR":
            factor = int(line[2:6] or "1") if line[2:6].strip() else 1
            if factor != 1:
                raise RinexError("OBS 含非单位 SYS / SCALE FACTOR，请先规范化观测值；此版本拒绝忽略缩放因子。")
        elif label == "END OF HEADER":
            body = i + 1
            break
    if body is None or not expected:
        raise RinexError("OBS 文件头不完整或没有观测类型。")
    for system, count in expected.items():
        if len(header["types"][system]) != count:
            raise RinexError(f"{system} 系统声明 {count} 个观测类型，实际读取数量不符。")
    if header["time_system"] not in ("GPS", "GAL", "QZS", "BDT", "UTC", "GLO", "IRN"):
        raise RinexError(f"不支持的观测时间系统：{header['time_system']}")
    if header["interval"] is not None and header["interval"] <= 0:
        raise RinexError("采样间隔必须为正数。")
    epochs, i = [], body
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if not line.startswith(">"):
            raise RinexError(f"OBS 第 {i+1} 行：应为历元头 >。")
        parts = line[1:].split()
        try:
            stamp = (datetime(*map(int, parts[:5]), tzinfo=timezone.utc) + timedelta(seconds=float(parts[5]))).timestamp()
            flag, count = int(parts[6]), int(parts[7])
        except (ValueError, IndexError) as exc:
            raise RinexError(f"OBS 第 {i+1} 行历元格式错误。") from exc
        i += 1
        if flag in (2, 3, 5):
            if i + count > len(lines):
                raise RinexError("特殊事件记录被截断。")
            warnings.append(f"跳过历元事件 flag={flag} 的 {count} 行描述。")
            i += count
            # Explicitly break arcs after receiver/antenna events.
            if epochs:
                epochs[-1].flag |= 2
            continue
        if flag not in (0, 1):
            raise RinexError(f"发现 flag={flag} 的动态头更新/周跳记录；请先用 RTKCONV 规范化后导入。")
        if epochs and stamp <= epochs[-1].time:
            raise RinexError("OBS 含重复或倒序历元；请先排序去重后导入。")
        epoch = Epoch(stamp, flag)
        for _ in range(count):
            if i >= len(lines) or not re.match(r"[GRECJSI]\d{2}", lines[i]):
                raise RinexError(f"OBS 第 {i+1} 行缺少卫星记录（文件可能被截断）。")
            sat, payload = lines[i][:3], lines[i][3:]
            source_line = i + 1
            if sat in epoch.satellites:
                raise RinexError(f"第 {source_line} 行出现重复卫星 {sat}。")
            types = header["types"].get(sat[0])
            if not types:
                raise RinexError(f"{sat} 未在文件头声明观测类型。")
            i += 1
            while i < len(lines) and lines[i].startswith("   ") and lines[i].strip():
                # Wrapped records are padded to a complete 16-character field.
                payload = payload.ljust(math.ceil(len(payload) / 16) * 16) + lines[i][3:]
                i += 1
            signals = {}
            for j, kind in enumerate(types):
                cell = payload[16*j:16*(j+1)].ljust(16)
                text = cell[:14].strip()
                try:
                    value = float(text.replace("D", "E")) if text else None
                    lli = int(cell[14]) if cell[14].strip() else 0
                except ValueError as exc:
                    raise RinexError(f"第 {source_line} 行 {sat} {kind} 数值或 LLI 非法。") from exc
                if value is not None and not math.isfinite(value):
                    raise RinexError(f"第 {source_line} 行出现非有限数值。")
                item = signals.setdefault(kind[1:], [None, None, None, None, 0])
                if kind[0] in "CLSD":
                    item["CLSD".index(kind[0])] = value if valid(value) else None
                if kind[0] == "L":
                    item[4] = lli
            epoch.satellites[sat] = signals
        epochs.append(epoch)
    if not epochs:
        raise RinexError("OBS 中没有普通观测历元。")
    if header["interval"] is None:
        header["interval"] = statistics.median(b.time-a.time for a,b in zip(epochs, epochs[1:])) if len(epochs)>1 else 0.0
    return Observation(header, epochs, list(dict.fromkeys(warnings)))


def validate_nav(path: Path):
    with path.open(encoding="ascii") as stream:
        line = stream.readline()
    if "RINEX VERSION / TYPE" not in line:
        raise RinexError("NAV 缺少 RINEX 文件头。")
    if not 2 <= float(line[:9]) < 4 or line[20:21] not in "NGLHJ":
        raise RinexError("当前支持 RINEX 2/3 广播星历 NAV 文件。")
