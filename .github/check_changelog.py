"""Fail a pull request that does not touch CHANGELOG.md.

The workflow passes LABELS (comma-separated) and BASE_SHA. The label
`skip-changelog` turns the check into a no-op.
"""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    labels = {item.strip() for item in os.environ.get("LABELS", "").split(",") if item.strip()}
    if "skip-changelog" in labels:
        print("Метка skip-changelog: проверка журнала пропущена.")
        return 0
    base = os.environ.get("BASE_SHA", "").strip()
    if not base:
        print("Не передан BASE_SHA, не с чем сравнить CHANGELOG.md.", file=sys.stderr)
        return 1
    diff = subprocess.check_output(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        text=True,
    )
    files = {line.strip() for line in diff.splitlines() if line.strip()}
    if "CHANGELOG.md" in files:
        print("CHANGELOG.md изменён.")
        return 0
    print(
        "Пул-реквест должен обновить CHANGELOG.md (раздел Unreleased).\n"
        "Если журнал менять не нужно, поставьте метку skip-changelog.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
