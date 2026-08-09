from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements.txt"
RUNTIME_DIR = Path(os.environ.get("MOKI_RUNTIME_DIR", ROOT / ".moki-runtime")).expanduser().resolve()
STAMP_FILE = RUNTIME_DIR / ".requirements-sha256"


def _runtime_python() -> Path:
    if os.name == "nt":
        return RUNTIME_DIR / "Scripts" / "python.exe"
    return RUNTIME_DIR / "bin" / "python"


def _requirements_digest() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def _dependencies_ready(python: Path) -> bool:
    if not python.exists():
        return False
    check = subprocess.run(
        [str(python), "-c", "import numpy, pandas, PIL"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return check.returncode == 0


def ensure_runtime() -> Path:
    python = _runtime_python()
    expected = _requirements_digest()
    installed = STAMP_FILE.read_text(encoding="utf-8").strip() if STAMP_FILE.exists() else ""
    if not python.exists():
        print(f"[MOKI] Creating isolated runtime at {RUNTIME_DIR}", file=sys.stderr)
        subprocess.run([sys.executable, "-m", "venv", str(RUNTIME_DIR)], check=True)
    if installed != expected or not _dependencies_ready(python):
        print("[MOKI] Installing pinned runtime dependencies", file=sys.stderr)
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-r",
                str(REQUIREMENTS),
            ],
            check=True,
        )
        STAMP_FILE.write_text(expected + "\n", encoding="utf-8")
    return python


def main() -> int:
    try:
        python = ensure_runtime()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"[MOKI] Runtime setup failed: {exc}", file=sys.stderr)
        print(
            "Install Python 3.10+ with venv support, confirm network access, then run this command again.",
            file=sys.stderr,
        )
        return 1
    command = [str(python), str(ROOT / "scripts" / "run_signal.py"), *sys.argv[1:]]
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
