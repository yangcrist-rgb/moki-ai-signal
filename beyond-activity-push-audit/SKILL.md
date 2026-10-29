---
name: beyond-activity-push-audit
description: >-
  扫描 Beyond 活动 Push（ActivityPushBubbleTable / 活动push_气泡推送表），
  可按大版本筛新增，也可全表检查所有版本的气泡与活动中心 Tab。
  核对 sort 去重、三服时区、临期早结束 3 天、Update 对齐阶段开放时间，输出本地 md/json。
  Use when the user asks to 扫 push、扫一下push配置、检查 push 气泡/Tab 配置、活动push表、临期三天、
  Update 阶段时间、三服时区对齐、全量 push、所有版本气泡页签、ActivityPushBubble。
---

# Beyond 活动 Push 气泡 / Tab 核对

对照活动时间窗与阶段表，核对 Bubble / Tab。无参数时自动认当前 Beyond 工程和 JSON 目录版本。`--all` 扫表内全部行。

打包：整个目录自包含，见 [SHARE.md](SHARE.md)。

## 何时使用

- `检查 push 气泡 Tab 配置`（含 v1d6 / v1d7 / 当前工程）
- `全量检查所有版本的气泡页签 push`
- `临期是不是结束前三天`
- `Update 要不要跟阶段更新时间一致`
- `三服时区有没有对齐`

## 工程根 / 版本怎么自动认

按顺序找 **Beyond 工程根**（有 `Data/TableCfg/JSON` 或 `Shared/Data/Tables`）：

1. `--project-root`
2. 本 skill 所在目录向上（拷在 `.claude/skills` 或 `.cursor/skills` 时即该工程）
3. Cursor 当前工作目录向上
4. 仅本机存在时，才读 `python-test` 的 `rule_project_config.json`

**版本**：未传 `--current-version` 时，用该工程 `Data/TableCfg/JSON/v1d6d99*` 推出 `v1d6`。

同事把 skill 放进自己工程、用 Cursor 打开该工程后，**零参数即可跑**。

## 报告写到哪

- skill 在某个 Beyond 工程内 → `<工程根>/Temp/ForAIReports/`
- 否则若有本机 python-test config → `D:\python-test\output\活动\reports\`
- 否则 → `<工程根>/Temp/ForAIReports/`
- `--output-dir` 可覆盖
- **禁止**改表 / 写 ForAI / P4，除非用户明确要求

## 一键命令

Cursor 已打开 Beyond 工程（skill 在工程内）——**优先这样跑，不要拼别人的盘符**：

```powershell
python .claude/skills/beyond-activity-push-audit/scripts/audit_activity_push.py
python .cursor/skills/beyond-activity-push-audit/scripts/audit_activity_push.py
python .claude/skills/beyond-activity-push-audit/scripts/audit_activity_push.py --all
```

本机 python-test 副本：

```powershell
python D:\python-test\skills\beyond-activity-push-audit\scripts\audit_activity_push.py --current-version v1d6
```

## Agent 执行步骤

1. 若工作区是 Beyond 工程：在工程根跑上面的相对路径脚本，**不要**加 `--project-root`，**不要**写死 `D:\python-test` 或 `F:\v1d6`。
2. 先找 `.claude/skills/beyond-activity-push-audit/scripts/audit_activity_push.py`，没有再找 `.cursor/skills/...`。
3. 用户说全量 → 加 `--all`；点名版本 → `--current-version`；只说检查/执行 → 零参数，让脚本自己认工程和版本。
4. 不要手写扫全库、不要改表。
5. 把 stdout 里的 `project=`、摘要、报告绝对路径发给用户。
6. 对 `error` 用一两句话说明怎么改。
7. 仅当用户明确要求改表 / 改代码 / 提交 P4 时才动工程文件。

## 自动核对项

按版本时 `versionStart` 匹配 `^CURRENT(d\d+)?$`。`--all` 不过滤。

数据：`~CsvCache/.../000_活动push_气泡推送表.csv` + `Data/TableCfg/JSON/<ver>d99*`。JSON 的 `ActivityPushBubbleTable` **没有** `versionStart`。

一览表含 **国服可弹**（`openTime + offset`）和 **国服可弹至**（`activityEndTime` 否则活动 `closeTime`）。

| 规则 | 内容 | 失败级 |
| --- | --- | --- |
| `sort_dup` | 同 activity 同 pushType 的 `bubbleSortId` 不能重复 | error |
| `sort_order` | 临期/End sort **小于** 已开放/Update | error |
| `end_minus_3d` | 临期/End 激活 = 结束点前整 **72h** | error |
| `update_vs_stage` | Update 激活 = 后续阶段开放时刻 | error |
| `end_vs_stage` | 临期/End 可弹至 = 某个阶段 closeTime（有阶段结束才核） | error |
| `tz_apac` / `tz_end` / `tz_update` | 亚太=国服；临期剩余时长三服一致；Update 对齐墙钟或开服后时长 | error |
| `pair` | 临期气泡 ↔ End Tab；更新气泡 ↔ Update Tab | warn |
| `activate_after_end` | 激活不得晚于结束点 | error |

阶段来源：CMS `timeId`/`timeOffset`、`{timeId}_season_*_week_*`、GameEntrance `timeOffset`。

另有辅助项：`activity_missing`（activityId 不在 Activity 表）、`time_missing`（算不出国服时间）、`parse`（offset / 时间写法无法解析）为 error；`unknown_type`（认不出 pushType）为 warn。

## 字段映射

字段名、pushType 取值、三服时区（默认国服/亚太 UTC+8、欧美 UTC-5）、offset 单位都在脚本的 `DEFAULT_SCHEMA`。工程实际不同时，在 `scripts/` 下放 `push_audit_schema.json`（或 `--schema <path>`）只写要覆盖的键，例如：

```json
{
  "push_type_map": {"1": "bubble_open", "2": "bubble_update", "3": "bubble_end", "11": "tab_update", "12": "tab_end"},
  "servers": {"NAEU": {"utc_offset_hours": -7}},
  "offset_unit_seconds": 1
}
```

看到大量 `unknown_type` / `time_missing` 时，优先补这份映射，不要改脚本逻辑。

## 输出文件名

```text
<ver>_push气泡Tab配置核对_<yyyymmdd>.md
<ver>_push气泡Tab配置核对_<yyyymmdd>.xlsx
all_push气泡Tab配置核对_<yyyymmdd>.md
```

xlsx 第一张「全量」：活动版本（开始 ~ 结束，无结束版本写常驻）、活动名、活动id（同活动合并）。本期新活动列出仍生效的全部气泡/Tab；早于本期开始的常驻，只列触发时间落在 `time_version_<版本>` 窗口内的 Push，没有则不列。活动按国服开启时间从早到晚分组，组内 Push 按触发时间排序，并带参照阶段与国服/欧美触发、结束。其后保留 Update对齐 / 结束对齐 / 问题，摘要在最后。需本机有 `openpyxl`。

## 前置

- Python 3.10+，标准库即可；导出 Excel 需要 `openpyxl`
- 工程已有气泡推送表 CSV 与导出 JSON
- [SHARE.md](SHARE.md)
