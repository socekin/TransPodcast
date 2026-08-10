"""Summarize an Evaluation Report as a compact, shareable extract.

A full report.json carries every segment and its per-word timestamps, which
is too large to paste into a discussion. This prints the summary, the
distribution of high-severity suspects, and the worst cases with their text
and timings, so a calibration question can be answered from the output alone.

Usage:
  uv run python scripts/evaluation_report_extract.py \
    reports/<publication-guid>/report.json [limit]
"""
import collections
import json
import sys
from pathlib import Path

path = sys.argv[1]
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 25
d = json.loads(Path(path).read_text(encoding="utf-8"))
seg = {s["segment_id"]: s for s in d["segments"]}

print("== episode ==")
print(json.dumps({**d["episode"], "partial_reasons": d["partial_reasons"]},
                 ensure_ascii=False))
print("== provenance ==")
print(json.dumps(d["provenance"], ensure_ascii=False))
print("== summary ==")
print(json.dumps(d["summary"], ensure_ascii=False))

print("\n== speaker distribution of high-severity suspects ==")
subj = collections.Counter()
dur = collections.Counter()
for f in d["findings"]:
    if f.get("suspect") and f.get("severity", 0) > 1:
        subj[f.get("subject_speaker", "?")] += 1
        s = f.get("source_seconds", 0)
        if s < 0.5:
            bucket = "<0.5s"
        elif s < 1:
            bucket = "0.5-1s"
        elif s < 2:
            bucket = "1-2s"
        else:
            bucket = ">=2s"
        dur[bucket] += 1
print("by speaker:", dict(subj))
print("by duration:", dict(dur))
words = collections.Counter(
    f.get("word_count", 0) for f in d["findings"]
    if f.get("suspect") and f.get("severity", 0) > 1
)
print("by word count:", dict(sorted(words.items())))

print(f"\n== top {limit} high-severity suspects ==")
shown = 0
for f in d["findings"]:
    if not (f.get("suspect") and f.get("severity", 0) > 1):
        continue
    if shown >= limit:
        break
    shown += 1
    print(f"\n[{shown}] {f['key']}  overlapping={f.get('overlapping')} "
          f"words={f.get('word_count')} {f.get('source_seconds', 0):.2f}s")
    for sid in f["segment_ids"]:
        s = seg.get(sid)
        if not s:
            continue
        pub = ("--" if s["publication_start"] is None
               else f"{s['publication_start']:.1f}")
        print(f"    {sid} [{s['speaker_name']}] src "
              f"{s['source_start']:.2f}-{s['source_end']:.2f} pub {pub} "
              f"xt={s['crosstalk']}")
        print(f"      EN {s['source_text'][:110]}")
        print(f"      ZH {(s['target_text'] or '(none)')[:110]}")
