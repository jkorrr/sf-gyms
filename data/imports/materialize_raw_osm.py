"""Materialize the immutable pre-research OSM fixture from repository history.

The canonical generator must never reuse its own generated output as input.
This one-time helper pins the original OSM-only fixture and validates that the
snapshot contains no web-research supplements before writing it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = ROOT / "data" / "imports" / "sf-gyms-osm-raw.json"
SOURCE_REVISION = "9dee34c:data/imports/sf-gyms-osm.json"


def main() -> int:
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", "show", SOURCE_REVISION],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    document = json.loads(completed.stdout)
    metadata = document.get("_meta", {})
    if metadata.get("source") != "OpenStreetMap" or metadata.get("supplementalSources"):
        raise ValueError("Pinned source revision is not an OSM-only fixture")
    metadata["immutableSourceRevision"] = SOURCE_REVISION
    OUTPUT_PATH.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT_PATH.relative_to(ROOT)), "gyms": len(document.get("gyms", []))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
