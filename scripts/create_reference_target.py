"""Create a disposable bare Git target for the isolated reference deployment."""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AREA = (ROOT / ".local" / "reference").resolve()
SOURCE = AREA / "source"
BARE = AREA / "sandbox.git"


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.DEVNULL)


def main() -> None:
    if not AREA.is_relative_to(ROOT.resolve()) or SOURCE.exists() or BARE.exists():
        raise RuntimeError("reference target must be a new path inside this workspace")
    SOURCE.mkdir(parents=True)
    git("init", "-b", "pact-sandbox", cwd=SOURCE)
    git("config", "user.name", "PACT disposable reference", cwd=SOURCE)
    git("config", "user.email", "pact@example.invalid", cwd=SOURCE)
    (SOURCE / "approved.txt").write_text("base\n", encoding="utf-8")
    git("add", "approved.txt", cwd=SOURCE)
    git("commit", "-m", "reference base", cwd=SOURCE)
    git("clone", "--bare", str(SOURCE), str(BARE), cwd=AREA)
    print("Created disposable reference target at .local/reference/sandbox.git")


if __name__ == "__main__":
    main()
