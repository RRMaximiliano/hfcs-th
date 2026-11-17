#!/usr/bin/env python3
"""Write config/targets_endline.csv from the confidential endline sample frame.

Run locally (the frame contains names and DNI and never enters this repository).
The output holds only school-level counts of baseline participants per role, which
are the denominators for endline follow-up coverage on the public dashboard.
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def project_root() -> Path:
    """Dropbox project folder (holds the confidential frame and data).

    Set TH_PROJECT_ROOT in this repository's .env (gitignored).
    """
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("TH_PROJECT_ROOT="):
                os.environ.setdefault("TH_PROJECT_ROOT", line.split("=", 1)[1].strip().strip('"').strip("'"))
    value = os.environ.get("TH_PROJECT_ROOT", "").strip()
    if not value:
        raise SystemExit("Set TH_PROJECT_ROOT (the Dropbox project folder) in .env")
    path = Path(value).expanduser()
    if not (path / "Instruments").is_dir():
        raise SystemExit(f"TH_PROJECT_ROOT does not look like the project folder: {path}")
    return path


ROLES = {"students": "students_frame.csv", "teachers": "teachers_frame.csv", "directors": "directors_frame.csv"}


def latest_frame() -> Path:
    preloads = project_root() / "Instruments" / "Sample" / "Preloads"
    frames = sorted(preloads.glob("ENDLINE_FRAME_v*"))
    if not frames:
        raise SystemExit(f"No ENDLINE_FRAME_v* folder in {preloads}")
    return frames[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=ROOT / "config" / "targets_endline.csv")
    args = parser.parse_args()
    frame_dir = args.frame_dir or latest_frame()

    with (frame_dir / "schools.csv").open(encoding="utf-8-sig", newline="") as handle:
        schools = list(csv.DictReader(handle))
    counts: dict[str, Counter[str]] = {}
    for role, filename in ROLES.items():
        with (frame_dir / filename).open(encoding="utf-8-sig", newline="") as handle:
            counts[role] = Counter(row["school_id"] for row in csv.DictReader(handle) if row.get("baseline_participant") == "1")

    known = {row["name"] for row in schools}
    for role, counter in counts.items():
        unknown = set(counter) - known
        if unknown:
            raise SystemExit(f"{role}: {len(unknown)} frame schools are missing from schools.csv")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["school_id", "school_name", "province", "target_students", "target_teachers", "target_directors"])
        for row in sorted(schools, key=lambda item: (item["province"], item["school_name"], item["name"])):
            sid = row["name"]
            writer.writerow([sid, row["school_name"], row["province"], counts["students"][sid], counts["teachers"][sid], counts["directors"][sid]])
    totals = {role: sum(counter.values()) for role, counter in counts.items()}
    print(f"Wrote {len(schools)} schools from {frame_dir.name} to {args.output} (baseline participants: {totals})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
