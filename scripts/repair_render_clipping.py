"""Repair a render checkpoint's stale clipping flag.

The assembly-time clipping flag used a stricter threshold (-0.99 dBTP)
than the accepted Quality Gate tolerance (-0.95 dBTP), so a healthy
master could be checkpointed with clipping=true and a retry would
discard the expensive render. This recomputes the flag under the
corrected rule for one Processing Run.

Usage: uv run python scripts/repair_render_clipping.py <run_id>
"""

from __future__ import annotations

import json
import sys

from transpodcast.config import TransPodcastSettings
from transpodcast.database import Database
from transpodcast.models import ProcessingRunRecord

CLIPPING_MAX_DBTP = -0.95


def main() -> None:
    run_id = int(sys.argv[1])
    database = Database(TransPodcastSettings().database_path)
    with database.session() as session:
        run = session.get(ProcessingRunRecord, run_id)
        if run is None or not run.render_json:
            print("Run or render checkpoint was not found")
            return
        checkpoint = json.loads(run.render_json)
        quality = checkpoint.get("quality", {})
        true_peak = float(quality.get("true_peak_dbtp", 0.0))
        before = bool(quality.get("clipping", False))
        after = true_peak > CLIPPING_MAX_DBTP
        quality["clipping"] = after
        checkpoint["quality"] = quality
        run.render_json = json.dumps(checkpoint)
        print(
            f"run #{run_id}: true_peak {true_peak} dBTP, "
            f"clipping {before} -> {after}"
        )


if __name__ == "__main__":
    main()
