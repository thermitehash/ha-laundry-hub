"""Fail if the mutmut score is below the target (default 85%)."""

from __future__ import annotations

import json
from pathlib import Path
import sys

TARGET = float(sys.argv[1]) if len(sys.argv) > 1 else 85.0

stats = json.loads(Path("mutants/mutmut-cicd-stats.json").read_text())
total = stats["total"] - stats.get("skipped", 0)
killed = stats["killed"] + stats.get("timeout", 0)
score = 100.0 * killed / total if total else 0.0
print(f"Mutation score: {killed}/{total} = {score:.1f}% (target {TARGET:.0f}%)")
print(json.dumps(stats, indent=2))
sys.exit(0 if score >= TARGET else 1)
