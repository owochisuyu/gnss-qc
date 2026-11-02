# GNSS Observatory — 观测数据质量工作台

输入一份 OBS 和一份 NAV，在本机网页中分析 GNSS 观测数据质量。界面借鉴 RTKPLOT 的观测、DOP、信噪比和多路径视图；计算直接调用随项目保存的官方 RTKLIB 2.4.3 b34 广播星历与几何函数。

## 启动

Windows 双击 **Start.cmd**，浏览器打开 <http://127.0.0.1:8765>。当前机器已构建 `native/rtkqc.dll`，已有所需 Python 依赖。

也可在本目录运行：

```powershell
python launch.py
```

不自动打开浏览器、在终端前台运行服务：

```powershell
python server.py
```

前台服务按 Ctrl+C 关闭。`launch.py` 启动的后台服务关闭浏览器后仍运行；可在 Windows 任务管理器中找到命令行为本目录 `server.py` 的 Python 进程并结束。再次双击启动器会复用已运行的实例。

迁移到另一台机器时需 Python 3.10+：

```powershell
python -m pip install -r requirements.txt
```

已附 Windows x64 库。修改 C 源码、换平台或缺少库时，需要 GCC/MinGW-w64：

```powershell
python build_native.py
```

没有 Node/npm、前端构建、外部 CDN、API Key 或互联网连接要求。默认仅监听 `127.0.0.1`。

## 使用

1. 点击 **导入 OBS / NAV**，选择或拖放文件，点击 **开始分析**。
2. 工作区的六组原始数据可从左侧 **打开本地示例** 直接使用；不会修改原文件。
3. 左侧切换总览、卫星时序、周跳、DOP、信噪比、多路径和天空图。
4. 上方的系统按钮筛选图表；两个时间滑块或图表滚轮同步缩放时间，双击图表或点击 **重置** 恢复全时段。顶部 KPI 始终是当前分析的全文件统计。
5. 多路径页面选择卫星及信号对，同时查看 MP1/MP2、ΔGF、ΔMW。卫星可见性图或周跳明细行可跳转到该星；事件行会定位到事件前后 15 分钟。
6. **分析设置** 可修改系统、截止高度角、GF/MW 阈值、断弧间隔、最短弧长和固定 ECEF 站址，保存后使用原文件重算。
7. 导出全时段历元 CSV、周跳候选事件 CSV、逐星组合 CSV 或完整 JSON；图表右上角按钮导出 PNG。CSV/JSON 保留全部原始计算历元，不受图表系统/时间筛选影响。

导入文件保存在本项目 `.cache/`；服务内保留最近四次完成的分析，旧分析的上传副本在下一次提交时清理。服务重启后需要重新导入；下载报告可长期保存。

## 支持范围

- OBS：未压缩的 **RINEX 3.x**，按每个字段的 16 字符读取，支持观测类型续行；`.obs`、`.26o`、`.rnx` 以内容识别。
- NAV：RTKLIB 支持的 RINEX 2/3 广播星历；GPS、GLONASS、Galileo、北斗、QZSS、NavIC、SBAS 由原生库处理。
- RINEX 2/4 OBS、CRINEX、压缩文件需先转换/解压。非单位观测缩放、动态头更新与专用周跳记录会明确报错，避免静默误读。
- 静态站或小范围运动数据。站址为固定位置，不提供逐历元运动轨迹定位；长距离运动的高度角/DOP 应接入逐历元位置后再分析。
- 每份上传文件最大 256 MB；计算在单个后台工作线程排队，以保护 RTKLIB 全局状态。原始观测与完整结果保留在内存，特别长的高频数据建议按观测时段拆分。

## 指标定义

### 可见性、缺测与信噪比

有效卫星历元要求至少一个非零有限数值观测。纯空行和仅有空相位 LLI 的记录不表示卫星可见。时间缺口按实际历元间隔识别，图形不跨缺口连线。

C/N0 使用 RINEX `Sxx` 字段，单位 dB-Hz。系统曲线在每历元对该系统所有有效 S 字段取算术均值；逐信号页面可改为同一 `Sxx`。总均值按全部有效 S 字段加权，缺测不填零。卫星表的 C/N0 则先作卫星每历元均值再平均，在页面中另行注明。

### GF、MW 与 LLI

`L1/L2` 为周，`P1/P2` 为米，`f1/f2` 为 Hz，`λ=c/f`：

```text
GF = λ1 L1 − λ2 L2
MW = c (L1 − L2)/(f1 − f2) − (f1 P1 + f2 P2)/(f1 + f2)
```

两种组合及其历元差的单位均为 **米**。默认 `|ΔGF| > 0.05 m`、`|ΔMW| > 10 m`。MW 默认值来自 RTKLIB `ppp.c` 的 `THRES_MW_JUMP`，GF 是本工具可调默认值。只比较相邻且未跨缺口、接收机事件的有效载波历元；GF 不强制有伪距，MW 必须同时具备两频伪距。

每颗卫星按有效相位覆盖率选择各频带的固定信号，覆盖相同则按代码排序；不会随历元跳换信号。GPS 以 L1、GLONASS 以 G1、Galileo 以 E1、北斗优先 B1I 作为锚，与其他可用频带分别组合。不是穷举所有频带两两配对。GLONASS 载频优先采用 OBS 头部槽号，否则由 RTKLIB NAV 取得。

有效相位的 `LLI & 3` 标记候选事件：bit0 是失锁、可能周跳；bit1 是半周不确定。空相位的 bit0 单独计数。合计事件按 **卫星＋历元** 去重，GF/MW/LLI 分项可重叠。GF/MW 的可比较样本数是 **信号对历元**，与卫星历元不是同一分母。

候选事件率 = 去重候选事件 / 有数值的卫星历元 × 1000，仅作描述，不等于经过真值验证的周跳率；GF 会受电离层变化影响，MW 会受码噪声影响。

### PDOP

原样调用 RTKLIB `dops()`：有实际观测、健康星历、有效几何且高度角达到截止的卫星进入四参数（位置＋单公共钟差）几何矩阵。少于四星、矩阵奇异或缺星历输出 `null`，不绘制成零。系统筛选后的 PDOP 采用该系统独立的卫星集合。

这是 RTKPLOT 风格的几何指标，不代表增加系统间钟差未知数后的多系统 SPP 估计精度。方位角/高度角与 RTKPLOT 头部站址模式一样，在接收历元调用广播星历 `satpos()`。站址优先采用手动输入、OBS 头部坐标，缺失时在前 100 历元中寻找首个成功 RTKLIB 单点解；失败则保留观测指标并明确停用几何指标。所有输出时间统一为 **GPST**，不是 UTC 或北京时间。

### 多路径

```text
α = (f1/f2)^2
MP1 = P1 − λ1 L1 − 2 GF/(α−1)
MP2 = P2 − λ2 L2 − 2 α GF/(α−1)
```

组合源于 RTKPLOT `UpdateMp()`。本工具在信号对缺相位、相邻历元不连续、间隔超过 1.5 倍采样间隔、接收机事件或任一 GF/MW/LLI 标记处断弧；缺伪距会单独切断对应 MP 分量的弧段。各弧去均值，默认不足 5 个样本的弧段输出 `null`，不以零参加 RMS。

**本工具与 RTKPLOT 的分弧规则不同**，不宣称 MP RMS 与原 RTKPLOT 逐值一致。MP 包含码噪声等误差，并非纯多路径。全局 RMS 合并所有有效“卫星－信号对－历元”，同一锚频可能在多个组合中出现；跨站比较请固定信号、阈值、高度角条件和样本范围。

## 实现与来源

```text
web/                  原生 JavaScript / Canvas 前端，无外部依赖
server.py             FastAPI、异步分析队列、上传、结果与导出
qc/rinex.py           保留空相位 LLI 的严格 RINEX 3 观测解析
qc/analysis.py        连续性、GF/MW/LLI、分弧、多路径与统计
qc/native.py          ctypes 接口，C 数据结构不暴露到 Python
native/bridge.c       RTKLIB 薄封装
vendor/rtklib/         官方源码、许可证与固定版本 SHA-256 清单
tests/                科学不变量、注入测试与六站数据验证
outputs/              本机验证记录与服务日志
```

官方仓库固定提交：[`180043ee24b6d2b168f98b64be15f69d50046b1a`](https://github.com/tomojitakasu/RTKLIB/tree/180043ee24b6d2b168f98b64be15f69d50046b1a)。源码文件未修改，版权及许可保留于 `vendor/rtklib/LICENSE.txt`。

- [`ppp.c`](https://github.com/tomojitakasu/RTKLIB/blob/180043ee24b6d2b168f98b64be15f69d50046b1a/src/ppp.c)：`gfmeas()`、`mwmeas()`、LLI 判据。
- [`plotdata.cpp`](https://github.com/tomojitakasu/RTKLIB/blob/180043ee24b6d2b168f98b64be15f69d50046b1a/app/winapp/rtkplot/plotdata.cpp)：`UpdateObs()`、`UpdateMp()`。
- `rtkcmn.c`、`ephemeris.c`、`rinex.c`：频率、DOP、坐标变换、星历读取与传播。

## 验证

```powershell
python -m unittest discover -s tests -p test_quality.py -v
python tests/validate_samples.py
```

14 项科学与边界测试已通过，包括人工注入一周周跳、码噪声/电离层消除不变量、缺测断弧、空相位 LLI、短弧排除、独立矩阵 PDOP、GLONASS 载频、时间系统转换和实际 SPP 回退。

Z061–Z066 六站逐一完整计算；历元数、有效历元数、有效相位数、空/非空相位 LLI、平均卫星数以及逐信号 C/N0 样本数/均值，均与工作区已有独立审计 `quality_audit_20261029/metrics.json` 一致。结果见 `outputs/sample_validation.json`，包含输入文件 SHA-256。

这验证了已覆盖算法和当前六组输入；未作现场真值周跳标注验证，也不把候选数量当成真实周跳次数。
