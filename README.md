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
| `manifest.json` | 策略、模板、数据截止日、文件路径与 SHA-256，供复核。 |

固定视觉模板为 `moki-ai-signal-v1`。相同的输入数据、参数和品牌资源会生成相同的图片字节结果。

## 快速开始

### 1. 环境

- Python 3.10 或更高版本
- 可访问 Yahoo Finance 日线接口的网络环境

```powershell
git clone <你的 GitHub 仓库地址>
cd moki-ai-signal
python -m pip install -r requirements.txt
```

### 2. 生成一张图

在项目根目录运行：

```powershell
python scripts\run_signal.py AEHR --output-dir .\outputs --cache-dir .\.moki-cache
```

程序会在终端打印 JSON 摘要，并将文件写入类似路径：

```text
outputs\AEHR\2026-08-07\moki-ai-signal.png
```

默认展示 93 个日历日，数据截止到最新一个已完成的美股交易时段。美东交易日 16:15 前运行时，默认会使用前一日作为截止日，避免把未收盘 K 线混入结果。

## 常用示例

### 复现指定历史日期

用于复核、演示或比较不同机器上的输出：

```powershell
python scripts\run_signal.py BABA --as-of 2026-08-07 --output-dir .\outputs --cache-dir .\.moki-cache
```

### 改变图表可见窗口

策略仍使用完整历史数据计算，只将图上窗口改为 140 天：

```powershell
python scripts\run_signal.py INTC --days 140 --output-dir .\outputs --cache-dir .\.moki-cache
```

### 强制刷新行情

默认下载失败时会使用本地缓存；`--refresh` 会禁止该回退，因此适合需要确认行情抓取成功的流程：

```powershell
python scripts\run_signal.py AEHR --refresh --output-dir .\outputs --cache-dir .\.moki-cache
```

### 在 Codex 中调用

将本目录安装为 Codex Skill 后，可直接提出：

```text
用 $moki-ai 为 BABA 生成 MOKI AI Signal 图，并给出最新已确认信号和待确认价位。
```

## 策略与数据边界

- 数据：通过 Yahoo Finance chart 接口获取美股日线 OHLCV；代码版本 1 仅支持美国股票日线。
- 默认策略：`s1-reconstruction@1.0.0`，参数为 RSI(14)、8% ZigZag 阈值、枢轴吸附窗口 ±2 根 K 线。
- 输出信号：仅输出已确认事件；`pending` 字段代表当前尚未确认的反转水平，不是交易指令。
- 复现口径：数据可能因供应商修订、停牌、拆股呈现或缓存状态而改变。请保存 `manifest.json`、`ohlcv.csv` 和 `signals.csv` 后再对照结论。

策略扩展必须遵循 [`references/strategy-contract.md`](references/strategy-contract.md)：新增策略不能修改现有 `s1-reconstruction@1.0.0` 的行为。视觉变动必须创建新的模板版本，不能覆盖 `moki-ai-signal-v1`。

## 项目结构

```text
agents/openai.yaml               # Codex Agent 元数据
assets/template-v1.json          # 固定图表模板与品牌色
assets/brand/                    # MOKI 品牌资源
references/                      # 策略与品牌契约
scripts/run_signal.py            # 命令行入口
scripts/moki_signal/             # 数据、策略、渲染和输出流水线
```

如果提供正式透明横版标识，将其命名为 `assets/brand/moki-logo-horizontal.png`。渲染器会自动优先使用该文件；否则会从已提供的 MOKI 品牌板提取同一锁定组合。

## 验证方式

项目内置的输出清单可用于逐次复核。对于完全相同的 `ticker`、`as-of`、日线数据、参数和品牌资源：

1. 比较 `signals.csv` 的 SHA-256，确认信号序列一致；
2. 比较 `moki-ai-signal.png` 的 SHA-256，确认图表字节一致；
3. 用 `manifest.json` 记录数据截止日、模板版本、策略版本与输出路径。

## 风险与授权

本项目仅用于研究、学习和产品开发，不构成证券、投资或交易建议。历史信号不代表未来表现；任何使用者须自行判断风险，并遵守适用的证券法规和数据供应商条款。

本仓库中的 MOKI 自有代码以 [MIT License](LICENSE) 发布。该许可证仅授予项目权利人能够授予的权利；已获授权的上游策略资料、行情数据及品牌素材如适用其他权利或使用条款，仍须遵守其各自约束，不因 MIT 自动取得再授权。
