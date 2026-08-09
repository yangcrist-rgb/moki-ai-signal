# MOKI AI Signal

MOKI AI Signal 是一个可扩展的美股日线信号图生成器：输入一个美股代码，程序下载日线 OHLC 数据，运行可审计的 `s1-reconstruction` 策略，并导出固定版式的 MOKI AI Signal 图片、信号明细和可复现清单。

> 重要说明：`s1-reconstruction` 是基于已获授权的公开源码做出的复现实现，不应表述为完整官方 S1 策略或投资建议。图上的 B/S 标记画在枢轴 K 线上，但只有在后续确认日才可知；它们不是当日可实时交易的提示。

## 你会得到什么

每次运行在 `outputs/<TICKER>/<最后交易日>/` 生成四个文件：

| 文件 | 用途 |
| --- | --- |
| `moki-ai-signal.png` | 固定 1920×1128 的 MOKI AI Signal 图，含 MOKI 标识。 |
| `signals.csv` | 已确认 B/S 信号，保留枢轴日与确认日。 |
| `ohlcv.csv` | 本次计算所用的日线 OHLCV 数据。 |
| `manifest.json` | 策略、模板、数据截止日、Yahoo/Sina 日线核对状态、文件路径与 SHA-256，供复核。 |

固定视觉模板为 `moki-ai-signal-v1`。相同的输入数据、参数和品牌资源会生成相同的图片字节结果。

## 快速开始

### 1. 环境

- Python 3.10 或更高版本
- 可访问 Yahoo Finance 日线接口的网络环境；新浪日线核对为尽力执行，失败不会阻断出图

```powershell
git clone https://github.com/linjiahao068-source/moki-ai-signal.git
cd moki-ai-signal
```

不需要安装 `global-stock-data`，也不需要先把 `numpy`、`pandas` 或 `Pillow` 安装到系统 Python。首次运行入口会在仓库内创建隔离的 `.moki-runtime` 并安装锁定版本；后续运行直接复用。

### 2. 生成一张图

在项目根目录运行：

```powershell
python scripts\bootstrap_signal.py AEHR --output-dir .\outputs --cache-dir .\.moki-cache
```

macOS/Linux 使用同一条命令，只需把路径分隔符改为 `/`。如果调用环境已经执行过 `python -m pip install -r requirements.txt`，也可直接使用底层入口 `scripts/run_signal.py`。

程序会在终端打印 JSON 摘要，并将文件写入类似路径：

```text
outputs\AEHR\2026-08-07\moki-ai-signal.png
```

默认展示 93 个日历日，数据截止到最新一个已完成的美股交易时段。美东交易日 16:15 前运行时，默认会使用前一日作为截止日，避免把未收盘 K 线混入结果。

## 常用示例

### 复现指定历史日期

用于复核、演示或比较不同机器上的输出：

```powershell
python scripts\bootstrap_signal.py BABA --as-of 2026-08-07 --output-dir .\outputs --cache-dir .\.moki-cache
```

### 改变图表可见窗口

策略仍使用完整历史数据计算，只将图上窗口改为 140 天：

```powershell
python scripts\bootstrap_signal.py INTC --days 140 --output-dir .\outputs --cache-dir .\.moki-cache
```

### 强制刷新行情

默认下载失败时会使用本地缓存；`--refresh` 会禁止该回退，因此适合需要确认行情抓取成功的流程：

```powershell
python scripts\bootstrap_signal.py AEHR --refresh --output-dir .\outputs --cache-dir .\.moki-cache
```

### 在 Codex 中调用

把整个仓库安装到 Codex Skills 目录，而不是只复制 `SKILL.md`：

```powershell
git clone https://github.com/linjiahao068-source/moki-ai-signal.git "$env:USERPROFILE\.codex\skills\moki-ai"
```

下一轮对话即可提出：

```text
用 $moki-ai 为 BABA 生成 MOKI AI Signal 图，并给出最新已确认信号和待确认价位。
```

MOKI 会调用仓库自带的 `scripts/bootstrap_signal.py`。因此新用户只安装这一个仓库即可，不需要另装 `global-stock-data`。

### 在其他编程 Agent/IDE 中调用

- 支持 Agent Skills 的工具：把整个仓库克隆到该工具认可的 Skills 目录；不要只复制 `SKILL.md`。
- 不支持 Agent Skills 的工具：把仓库作为项目打开，让 Agent 遵循根目录的 `SKILL.md`，或直接运行 `scripts/bootstrap_signal.py`。
- 工具必须能够执行本地 Python 并允许首次运行下载锁定依赖。仅能聊天、不能执行本地命令的软件无法生成文件。

## 策略与数据边界

- 主数据：通过 Yahoo Finance chart 接口获取美股日线 OHLCV；代码版本 1 仅支持美国股票日线。
- 独立核对：使用仓库内置的新浪美股日 K 解析器核对最近 10 个重叠交易日的 OHLC。它不依赖外部 Skill，也不会替换或修改策略输入；结果为 `passed`、`warning` 或 `unavailable` 并写入 `manifest.json`。
- 默认策略：`s1-reconstruction@1.0.0`，参数为 RSI(14)、8% ZigZag 阈值、枢轴吸附窗口 ±2 根 K 线。
- 输出信号：仅输出已确认事件；`pending` 字段代表当前尚未确认的反转水平，不是交易指令。
- 复现口径：数据可能因供应商修订、停牌、拆股呈现或缓存状态而改变。请保存 `manifest.json`、`ohlcv.csv` 和 `signals.csv` 后再对照结论。

详细核对口径见 [`references/data-contract.md`](references/data-contract.md)。策略扩展必须遵循 [`references/strategy-contract.md`](references/strategy-contract.md)：新增策略不能修改现有 `s1-reconstruction@1.0.0` 的行为。视觉变动必须创建新的模板版本，不能覆盖 `moki-ai-signal-v1`。

## 项目结构

```text
agents/openai.yaml               # Codex Agent 元数据
assets/template-v1.json          # 固定图表模板与品牌色
assets/brand/                    # MOKI 品牌资源
licenses/                        # 获许可上游代码的许可证副本
references/                      # 数据、策略与品牌契约
scripts/bootstrap_signal.py      # 跨工具首次运行与隔离依赖入口
scripts/run_signal.py            # 已配置 Python 环境的底层入口
scripts/moki_signal/             # 数据、策略、渲染和输出流水线
tests/                           # 离线数据解析与核对测试
```

如果提供正式透明横版标识，将其命名为 `assets/brand/moki-logo-horizontal.png`。渲染器会自动优先使用该文件；否则会从已提供的 MOKI 品牌板提取同一锁定组合。

## 验证方式

项目内置的输出清单可用于逐次复核。对于完全相同的 `ticker`、`as-of`、日线数据、参数和品牌资源：

1. 比较 `signals.csv` 的 SHA-256，确认信号序列一致；
2. 比较 `moki-ai-signal.png` 的 SHA-256，确认图表字节一致；
3. 查看 `manifest.json` 的 `data.validation`，确认第二数据源核对状态；
4. 用 `manifest.json` 记录数据截止日、模板版本、策略版本与输出路径。

## 风险与授权

本项目仅用于研究、学习和产品开发，不构成证券、投资或交易建议。历史信号不代表未来表现；任何使用者须自行判断风险，并遵守适用的证券法规和数据供应商条款。

本仓库中的 MOKI 自有代码以 [MIT License](LICENSE) 发布。日线核对器中改编自 `global-stock-data` 的部分保留 Apache-2.0 许可、作者署名与修改说明，详见 [`licenses/global-stock-data-APACHE-2.0.txt`](licenses/global-stock-data-APACHE-2.0.txt) 和 [`references/data-contract.md`](references/data-contract.md)。项目方确认已取得集成这些代码及当前 Yahoo/Sina 数据访问方式所需的完整书面授权；本仓库不分发市场数据，授权文件本身未随仓库公开。

任何上游策略资料、行情数据及品牌素材仍受相应授权范围与条款约束，不因 MOKI 的 MIT 许可证自动取得额外再授权。
