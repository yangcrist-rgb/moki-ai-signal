from __future__ import annotations

import csv
import datetime as dt
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "audit_activity_push.py"
sys.path.insert(0, str(SCRIPT.parent))

import audit_activity_push as audit  # noqa: E402

DAY = 86400
TIME_RANGES = {
    "time_version_v1d6": ("2026-10-01 04:00:00", "2026-11-12 04:00:00"),
    "time_act_a": ("2026-10-01 04:00:00", "2026-10-29 04:00:00"),
    "time_act_a_season_1_week_1": ("2026-10-01 04:00:00", "2026-10-08 04:00:00"),
    "time_act_a_season_1_week_2": ("2026-10-08 04:00:00", "2026-10-15 04:00:00"),
    "time_old": ("2026-06-01 04:00:00", "2027-01-01 04:00:00"),
}
ACTIVITIES = {
    "act_a": {"id": "act_a", "name": "新活动A", "timeId": "time_act_a", "versionStart": "v1d6"},
    "act_old": {"id": "act_old", "name": "常驻B", "timeId": "time_old", "versionStart": "v1d5"},
}
HEADER = ["id", "activityId", "pushType", "bubbleSortId", "offset", "activityEndTime", "versionStart"]
CSV_ROWS = [
    ["string", "string", "string", "int", "int", "string", "string"],
    ["编号", "活动id", "类型", "排序", "偏移", "结束", "版本"],
    ["p1", "act_a", "BubbleOpen", "10", "0", "", "CURRENT"],
    ["p2", "act_a", "BubbleUpdate", "20", str(7 * DAY), "", "CURRENT"],
    ["p3", "act_a", "BubbleEnd", "5", str(25 * DAY), "", "CURRENT"],
    ["p4", "act_a", "TabEnd", "5", str(25 * DAY), "", "CURRENT"],
    ["p5", "act_a", "TabUpdate", "20", str(7 * DAY + 3600), "", "CURRENTd1"],
    ["p6", "act_old", "BubbleEnd", "30", str(212 * DAY), "", "CURRENTd1"],
    ["p7", "act_old", "BubbleOpen", "30", "0", "", "v1d5"],
    ["p8", "act_a", "BubbleOpen", "10", str(DAY), "", "CURRENT"],
]


def build_project(root: Path) -> None:
    json_dir = root / "Data" / "TableCfg" / "JSON" / "v1d6d99d1"
    json_dir.mkdir(parents=True)
    ranges = {k: {"id": k, "startTime": s, "endTime": e} for k, (s, e) in TIME_RANGES.items()}
    (json_dir / "TimeRangeTable.json").write_text(json.dumps({"TimeRangeTable": ranges}), encoding="utf-8")
    (json_dir / "ActivityTable.json").write_text(json.dumps({"ActivityTable": ACTIVITIES}), encoding="utf-8")
    csv_dir = root / "Shared" / "Data" / "Tables" / "~CsvCache" / "活动"
    csv_dir.mkdir(parents=True)
    with (csv_dir / audit.CSV_NAME).open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADER)
        writer.writerows(CSV_ROWS)


def run(args: list[str], cwd: Path, script: Path = SCRIPT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=False
    )


def load_report(out_dir: Path) -> dict:
    reports = sorted(out_dir.glob("*_push气泡Tab配置核对_*.json"))
    assert len(reports) == 1, reports
    return json.loads(reports[0].read_text(encoding="utf-8"))


class ParserTests(unittest.TestCase):
    def test_duration_formats(self) -> None:
        self.assertEqual(audit.parse_duration("259200", 1), dt.timedelta(days=3))
        self.assertEqual(audit.parse_duration("3d", 1), dt.timedelta(days=3))
        self.assertEqual(audit.parse_duration("1d 2h", 1), dt.timedelta(hours=26))
        self.assertEqual(audit.parse_duration("1:02:00:00", 1), dt.timedelta(hours=26))
        self.assertEqual(audit.parse_duration("", 1), dt.timedelta(0))
        with self.assertRaises(ValueError):
            audit.parse_duration("abc", 1)

    def test_naive_time_uses_server_zone(self) -> None:
        cn = dt.timezone(dt.timedelta(hours=8))
        us = dt.timezone(dt.timedelta(hours=-5))
        self.assertEqual(audit.parse_time("2026-10-01 04:00:00", cn), dt.datetime(2026, 9, 30, 20, tzinfo=dt.timezone.utc))
        self.assertEqual(audit.parse_time("2026-10-01 04:00:00", us), dt.datetime(2026, 10, 1, 9, tzinfo=dt.timezone.utc))
        self.assertEqual(audit.parse_time(1790000000000, cn), dt.datetime.fromtimestamp(1790000000, dt.timezone.utc))


class AuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "Beyond"
        build_project(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_version_mode_flags_expected_rules(self) -> None:
        out = self.tmp / "out"
        result = run(["--project-root", str(self.root), "--output-dir", str(out)], cwd=self.tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"project={self.root}", result.stdout)
        self.assertIn("version=v1d6", result.stdout)

        report = load_report(out)
        found = {(i["rule"], tuple(i["push_ids"])) for i in report["issues"]}
        self.assertIn(("sort_dup", ("p1", "p8")), found)
        self.assertIn(("update_vs_stage", ("p5",)), found)
        self.assertIn(("end_minus_3d", ("p6",)), found)
        self.assertIn(("sort_order", ("p6", "p7")), found)
        self.assertIn(("pair", ("p6",)), found)
        self.assertEqual(len(report["issues"]), 5, report["issues"])
        self.assertEqual({i["pushId"] for i in report["items"]}, {"p1", "p2", "p3", "p4", "p5", "p6", "p8"})

        by_id = {i["pushId"]: i for i in report["items"]}
        self.assertEqual(by_id["p2"]["stageRef"], "time_act_a_season_1_week_2")
        self.assertEqual(by_id["p3"]["remain"], "72h")
        self.assertEqual(by_id["p3"]["result"], "ok")
        self.assertEqual({r["pushId"] for r in report["overview"]}, {"p1", "p2", "p3", "p4", "p5", "p8"})
        self.assertTrue(list(out.glob("v1d6_push气泡Tab配置核对_*.md")))

    def test_all_mode_checks_every_row(self) -> None:
        out = self.tmp / "out"
        result = run(["--project-root", str(self.root), "--output-dir", str(out), "--all", "--fail-on-error"], cwd=self.tmp)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = load_report(out)
        self.assertEqual(len(report["items"]), 8)
        self.assertTrue(list(out.glob("all_push气泡Tab配置核对_*.md")))

    def test_zero_args_inside_project(self) -> None:
        skill = self.root / ".claude" / "skills" / "beyond-activity-push-audit"
        shutil.copytree(SKILL_DIR / "scripts", skill / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        result = run([], cwd=self.tmp, script=skill / "scripts" / "audit_activity_push.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("skill 目录", result.stdout)
        load_report(self.root / "Temp" / "ForAIReports")

    def test_schema_override_maps_push_types(self) -> None:
        schema = self.tmp / "schema.json"
        schema.write_text(json.dumps({"push_type_map": {"BubbleUpdate": "bubble_open"}}), encoding="utf-8")
        out = self.tmp / "out"
        result = run(["--project-root", str(self.root), "--output-dir", str(out), "--schema", str(schema)], cwd=self.tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rules = {i["rule"] for i in load_report(out)["issues"]}
        self.assertIn("pair", rules)
        kinds = {i["pushId"]: i["kind"] for i in load_report(out)["items"]}
        self.assertEqual(kinds["p2"], "bubble_open")

    def test_missing_project_reports_error(self) -> None:
        result = run(["--project-root", str(self.tmp / "nope")], cwd=self.tmp)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error=", result.stdout)


if __name__ == "__main__":
    unittest.main()
