"""Compare old per-recording writes with batch sync writes using synthetic data.

Run: .venv/bin/python scripts/benchmark-storage.py
Only temporary databases are written. Results measure local SQLite writing,
not Cloud latency, app startup, or a statistically representative workload.
"""

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.models import PlaudFile  # noqa: E402
from core.storage import Storage  # noqa: E402


def main() -> None:
    rows = [
        PlaudFile(id=f"recording-{i}", filename=f"Synthetic recording {i}", edit_time=i)
        for i in range(2000)
    ]
    timings = {"per_recording": [], "batch": []}
    with tempfile.TemporaryDirectory(prefix="plaud-sync-benchmark-") as temp:
        for repeat in range(3):
            for mode, samples in timings.items():
                storage = Storage(Path(temp) / f"{mode}-{repeat}.db")
                start = time.perf_counter()
                if mode == "per_recording":
                    for row in rows:
                        storage.upsert_file(row, now=1)
                else:
                    storage.upsert_files(rows, now=1)
                samples.append(time.perf_counter() - start)
    print(
        json.dumps(
            {
                "synthetic_rows": len(rows),
                "seconds": timings,
                "median_seconds": {
                    mode: statistics.median(values) for mode, values in timings.items()
                },
                "scope": "SQLite write portion only; three samples on this host",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
