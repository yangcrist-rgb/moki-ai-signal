from __future__ import annotations

import argparse
import json
from pathlib import Path

from moki_signal.options_agent import render_agent_markdown, run_agent


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="McMillan-style options decision helper for holdings and watchlist symbols")
    parser.add_argument("--profile", type=Path, default=ROOT / "configs" / "options_agent_profile.json")
    parser.add_argument("--output-dir", type=Path, default=Path.cwd() / "outputs" / "options-agent")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    profile_path = args.profile
    if not profile_path.exists():
        example = ROOT / "configs" / "options_agent_profile.example.json"
        raise SystemExit(f"Profile not found: {profile_path}. Copy {example} to {profile_path} and edit it.")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    result = run_agent(profile, workers=args.workers)
    stamp = result["agent"]["generated_at_utc"][:10]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / f"{profile.get('name', 'profile')}-{stamp}.json"
    md_path = json_path.with_suffix(".md")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_agent_markdown(result), encoding="utf-8")
    print(json.dumps({"json": str(json_path.resolve()), "markdown": str(md_path.resolve()), "symbols": len(result["symbols"]), "warnings": result["warnings"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
