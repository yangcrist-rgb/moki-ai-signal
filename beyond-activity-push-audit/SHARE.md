# 分享安装说明

把压缩包解压到 **Beyond 工程根**（和 `Assets`、`Data` 同一层）。解压后应能看到：

```text
<工程根>/.claude/skills/beyond-activity-push-audit/SKILL.md
```

用 Cursor 重新打开这个工程，对 Agent 说「检查 push 配置」或「扫一下 push」。Agent 会跑本目录里的脚本，版本从工程 JSON 目录自己认，报告写到 `<工程根>/Temp/ForAIReports/`。不要传别人的盘符，也不要改表。

本文件夹自包含：标准库 Python 即可，**不依赖** `D:\python-test`。Excel 需要本机已装 `openpyxl`（`pip install openpyxl`）。没有的话仍会写出 md / json。

## 拷贝内容

```text
beyond-activity-push-audit/
  SKILL.md
  SHARE.md
  scripts/
    audit_activity_push.py
    push_audit_schema.json   # 可选，字段 / pushType / 时区映射，见 SKILL.md「字段映射」
  tests/                     # 可选，python -m unittest discover -s tests
```

## 推荐：直接放进 Beyond 工程（Cursor 打开即可零参数）

拷到下面任一位置：

```text
<Beyond工程根>/.claude/skills/beyond-activity-push-audit/
<Beyond工程根>/.cursor/skills/beyond-activity-push-audit/
```

用 Cursor **打开这个工程**，对 Agent 说「检查 push 配置」或在工程根执行：

```powershell
python .claude/skills/beyond-activity-push-audit/scripts/audit_activity_push.py
```

不用传 `--project-root`。脚本会：

- 从 skill 目录向上找到当前工程
- 从 `Data/TableCfg/JSON/vXXdNNd99*` 推出大版本
- 报告写到 `<工程根>/Temp/ForAIReports/`

全表再加 `--all`。

## 备选：Cursor 个人 skills

拷到 `%USERPROFILE%\.cursor\skills\beyond-activity-push-audit\` 后，仍要用 Cursor 打开 Beyond 工程（靠工作目录认根），或显式：

```powershell
python "$env:USERPROFILE\.cursor\skills\beyond-activity-push-audit\scripts\audit_activity_push.py" --project-root "<工程根>"
```

## 报告目录

| 情况 | 默认 |
| --- | --- |
| skill 在 Beyond 工程内 | `<工程根>\Temp\ForAIReports\` |
| 本机另有 python-test config（打包者自己跑） | `D:\python-test\output\活动\reports\` |

可用 `--output-dir` 覆盖。

## 依赖

- Python 3.10+
- `Shared/Data/Tables/~CsvCache/**/000_活动push_气泡推送表.csv`
- `Data/TableCfg/JSON/<ver>d99*`（Activity / TimeRange 等）
