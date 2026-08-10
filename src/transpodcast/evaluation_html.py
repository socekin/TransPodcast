"""Render an Evaluation Report as a self-contained local page.

The page carries no external assets and no network calls, so a generated
report stays openable long after the Processing Run that produced it.
Owner-facing strings come from the localization resource; this module
holds no display text of its own.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any

from transpodcast.evaluation import (
    CROSSTALK,
    INTERLEAVE_SUSPECT,
    MIDSPLIT_SUSPECT,
    EvaluationReport,
    Finding,
    ReportSegment,
    Verdict,
)

DEFAULT_LOCALE = "zh-CN"
_SPEAKER_PALETTE = (
    "#2563eb",
    "#16a34a",
    "#d97706",
    "#0891b2",
    "#7c3aed",
    "#be123c",
)


@lru_cache(maxsize=2)
def load_locale(locale: str = DEFAULT_LOCALE) -> dict[str, str]:
    path = Path(__file__).parent / "locales" / f"report.{locale}.json"
    parsed = json.loads(path.read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in parsed.items()}


def _clock(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    minutes, remainder = divmod(max(seconds, 0.0), 60)
    return f"{int(minutes):02d}:{remainder:04.1f}"


def report_payload(report: EvaluationReport) -> dict[str, Any]:
    """The report model as plain data, shared by the page and the sidecar."""
    return {
        "episode": asdict(report.episode),
        "provenance": asdict(report.provenance),
        "seek_available": report.seek_available,
        "partial": report.partial,
        "partial_reasons": [str(reason) for reason in report.partial_reasons],
        "summary": asdict(report.summary),
        "speakers": [list(item) for item in report.speakers],
        "source_events": [asdict(event) for event in report.source_events],
        "segments": [asdict(segment) for segment in report.segments],
        "findings": [
            {
                **asdict(finding),
                "kind": str(finding.kind),
                "stage": str(finding.stage),
                "verdict": (
                    str(finding.verdict) if finding.verdict is not None else None
                ),
                "key": finding.key,
                "suspect": finding.suspect,
            }
            for finding in report.findings
        ],
    }


def _embedded_json(payload: dict[str, Any]) -> str:
    """Serialize for an inline data block.

    Transcript text is untrusted, so a literal ``</script>`` must not be
    able to terminate the block that carries it.
    """
    return json.dumps(payload, ensure_ascii=False, indent=1).replace("</", "<\\/")


def _speaker_colours(report: EvaluationReport) -> dict[str, str]:
    return {
        label: _SPEAKER_PALETTE[index % len(_SPEAKER_PALETTE)]
        for index, (label, _name) in enumerate(report.speakers)
    }


def _evidence(finding: Finding, labels: dict[str, str]) -> str:
    if finding.kind == INTERLEAVE_SUSPECT:
        return labels["evidence_interleave"].format(
            words=finding.word_count,
            seconds=f"{finding.source_seconds:.1f}",
            middle=finding.subject_speaker,
            neighbour=finding.neighbour_speaker,
        )
    if finding.kind == MIDSPLIT_SUSPECT:
        return labels["evidence_midsplit"].format(
            neighbour=finding.neighbour_speaker,
        )
    return labels["evidence_crosstalk"].format(
        seconds=f"{finding.source_seconds:.1f}",
    )


def _findings_by_segment(report: EvaluationReport) -> dict[str, list[Finding]]:
    """Anchor each finding to the segment the Owner should listen to."""
    grouped: dict[str, list[Finding]] = {}
    for finding in report.findings:
        anchor = (
            finding.segment_ids[1]
            if len(finding.segment_ids) > 1
            else finding.segment_ids[0]
        )
        grouped.setdefault(anchor, []).append(finding)
    return grouped


def _verdict_buttons(finding: Finding, labels: dict[str, str]) -> str:
    buttons = "".join(
        '<button class="vb{selected}" data-key="{key}" data-verdict="{value}">'
        "{label}</button>".format(
            selected=" sel" if finding.verdict == value else "",
            key=escape(finding.key, quote=True),
            value=str(value),
            label=escape(labels[f"verdict_{value}"]),
        )
        for value in Verdict
    )
    return (
        f'<div class="verdict">'
        f'<span class="vlabel">{escape(labels["verdict_label"])}</span>'
        f"{buttons}</div>"
    )


def _segment_block(
    segment: ReportSegment,
    findings: list[Finding],
    labels: dict[str, str],
    colours: dict[str, str],
) -> str:
    kinds = sorted({str(finding.kind) for finding in findings})
    classes = ["seg"]
    if any(finding.suspect for finding in findings):
        classes.append("suspect")
    if segment.omitted_as_sponsor:
        classes.append("legacy-omitted")
    if segment.content_edit_state:
        classes.append(f"content-{segment.content_edit_state}")
    flags = "".join(
        f'<span class="flag {escape(kind, quote=True)}">'
        f'{escape(labels[f"kind_{kind}"])}</span>'
        for kind in kinds
    )
    if segment.omitted_as_sponsor:
        flags += (
            '<span class="flag legacy-omitted">'
            f'{escape(labels["segment_legacy_omitted"])}</span>'
        )
    if segment.content_edit_state:
        flags += (
            '<span class="flag content-edit">'
            f'{escape(labels[f"segment_content_{segment.content_edit_state}"])}'
            "</span>"
        )
    times = escape(
        f"{labels['segment_publication_time']} "
        f"{_clock(segment.publication_start)}–{_clock(segment.publication_end)}"
        f" · {labels['segment_source_time']} "
        f"{_clock(segment.source_start)}–{_clock(segment.source_end)}"
    )
    target = (
        f'<span class="zh"><span class="l">'
        f'{escape(labels["segment_target"])}</span>'
        f"{escape(segment.target_text or labels['segment_legacy_omitted'])}"
        "</span>"
    )
    evidence = ""
    if findings:
        items = "".join(
            "<li>"
            f'<b>{escape(labels[f"kind_{finding.kind}"])}</b> · '
            f'{escape(labels["evidence_stage"])}: '
            f'{escape(labels[f"stage_{finding.stage}"])} — '
            f"{escape(_evidence(finding, labels))}"
            f"{_verdict_buttons(finding, labels)}"
            "</li>"
            for finding in findings
        )
        words = ""
        if segment.words:
            rendered = "\n".join(
                f"{escape(text)}  {start:.2f}–{end:.2f}"
                for text, start, end in segment.words
            )
            words = (
                f'<div class="words"><b>{escape(labels["segment_words"])}</b>'
                f"<pre>{rendered}</pre></div>"
            )
        evidence = (
            f'<div class="evidence"><span class="etitle">'
            f'{escape(labels["evidence_label"])}</span>'
            f"<ul>{items}</ul>{words}</div>"
        )
    if segment.content_edit_state:
        category = labels[
            f"content_category_{segment.content_edit_category}"
        ]
        content_evidence = (
            '<div class="evidence content-edit-evidence">'
            f'<span class="etitle">{escape(category)}</span>'
            f'<p>{escape(segment.content_edit_rationale or "")}</p>'
            f'<p>{escape(labels["content_edit_evidence"])}: '
            f'{escape(" · ".join(segment.content_edit_evidence_segment_ids))}'
            "</p>"
            "</div>"
        )
        evidence += content_evidence
    colour = colours.get(segment.speaker_label, _SPEAKER_PALETTE[-1])
    seek = (
        f'data-seek="{segment.publication_start:.3f}" '
        if segment.publication_start is not None
        else ""
    )
    provisional = (
        f'<span class="prov">{escape(labels["speaker_provisional"])}</span>'
        if segment.speaker_provisional
        else ""
    )
    reviewed = "true" if any(item.verdict for item in findings) else "false"
    return (
        f'<div class="{" ".join(classes)}" '
        f'data-segment="{escape(segment.segment_id, quote=True)}" '
        f'data-kinds="{escape(",".join(kinds), quote=True)}" '
        f'data-reviewed="{reviewed}" {seek}>'
        f'<div class="seg-top">'
        f'<span class="seg-id">{escape(segment.segment_id)}</span>'
        f'<span class="spk" style="color:{colour};'
        f'background:{colour}18">{escape(segment.speaker_name)}</span>'
        f"{provisional}"
        f'<span class="times">{times}</span>{flags}</div>'
        f'<div class="texts">'
        f'<span class="en"><span class="l">'
        f'{escape(labels["segment_source"])}</span>'
        f"{escape(segment.source_text)}</span>{target}</div>"
        f"{evidence}</div>"
    )


def _timeline(
    report: EvaluationReport,
    colours: dict[str, str],
    labels: dict[str, str],
) -> str:
    """Lay out retained speech and Source Events in publication order."""
    suspects = report.suspect_segment_ids()
    cells = []
    for segment in report.segments:
        if (
            segment.omitted_as_sponsor
            or segment.content_edit_state == "excluded"
        ):
            continue
        colour = colours.get(segment.speaker_label, _SPEAKER_PALETTE[-1])
        span = 1.0
        if (
            segment.publication_start is not None
            and segment.publication_end is not None
        ):
            span = max(segment.publication_end - segment.publication_start, 0.2)
        marker = "⚠" if segment.segment_id in suspects else ""
        cells.append(
            f'<div style="flex:{span:.3f};background:{colour}" '
            f'data-segment="{escape(segment.segment_id, quote=True)}" '
            f'title="{escape(segment.segment_id, quote=True)}">'
            f"{marker}</div>"
        )
    events = "".join(
        f'<span class="event">{escape(event.kind)} '
        f"{_clock(event.start_seconds)}</span>"
        for event in report.source_events
    )
    event_row = (
        f'<div class="events"><b>{escape(labels["source_events_label"])}</b>'
        f"{events}</div>"
        if report.source_events
        else ""
    )
    return f'<div class="timeline">{"".join(cells)}</div>{event_row}'


def _summary_tiles(report: EvaluationReport, labels: dict[str, str]) -> str:
    summary = report.summary
    tiles = [
        (labels["summary_segments"], str(summary.segment_count)),
        (labels["summary_speakers"], str(summary.speaker_count)),
        (labels["summary_crosstalk"], str(summary.crosstalk_count)),
        (labels["summary_suspects"], str(summary.suspect_count)),
        (
            labels["summary_reviewed"],
            f"{summary.reviewed_count} / {len(report.findings)}",
        ),
    ]
    if summary.omitted_sponsor_count:
        tiles.append(
            (
                labels["summary_legacy_omissions"],
                str(summary.omitted_sponsor_count),
            )
        )
    if summary.semantic_exclusion_count:
        tiles.append(
            (
                labels["summary_semantic_exclusions"],
                (
                    f"{summary.semantic_exclusion_count} · "
                    f"{summary.semantic_excluded_source_seconds:.1f}s"
                ),
            )
        )
    return "".join(
        f'<div class="tile"><div class="v">{escape(value)}</div>'
        f'<div class="k">{escape(key)}</div></div>'
        for key, value in tiles
    )


def _filter_chips(report: EvaluationReport, labels: dict[str, str]) -> str:
    summary = report.summary
    chips = (
        ("all", labels["filter_all"], len(report.segments)),
        (str(CROSSTALK), labels["filter_crosstalk"], summary.crosstalk_count),
        (
            str(INTERLEAVE_SUSPECT),
            labels["filter_interleave"],
            summary.finding_counts.get(str(INTERLEAVE_SUSPECT), 0),
        ),
        (
            str(MIDSPLIT_SUSPECT),
            labels["filter_midsplit"],
            summary.finding_counts.get(str(MIDSPLIT_SUSPECT), 0),
        ),
        ("reviewed", labels["filter_reviewed"], summary.reviewed_count),
        (
            "unreviewed",
            labels["filter_unreviewed"],
            len(report.findings) - summary.reviewed_count,
        ),
    )
    return "".join(
        f'<button class="fchip{" on" if key == "all" else ""}" '
        f'data-filter="{escape(key, quote=True)}">'
        f"{escape(label)} ({count})</button>"
        for key, label, count in chips
    )


def render_report_html(
    report: EvaluationReport,
    *,
    audio_filename: str | None,
    locale: str = DEFAULT_LOCALE,
) -> str:
    labels = load_locale(locale)
    colours = _speaker_colours(report)
    grouped = _findings_by_segment(report)
    episode = report.episode
    provenance = report.provenance
    diarization_identity = provenance.diarization_model
    if provenance.diarization_model_revision:
        diarization_identity += f"@{provenance.diarization_model_revision}"
    notices = []
    if not report.seek_available:
        notices.append(labels["seek_unavailable"])
    if report.partial:
        reasons = "、".join(
            labels[f"partial_{reason}"] for reason in report.partial_reasons
        )
        notices.append(labels["partial_report"].format(reasons=reasons))
    notice = "".join(
        f'<div class="notice">{escape(text)}</div>' for text in notices
    )
    hint_key = "player_hint" if report.seek_available else "player_hint_no_seek"
    player = (
        f'<div class="player"><audio controls preload="none" id="player" '
        f'src="{escape(audio_filename, quote=True)}"></audio>'
        f'<span class="hint">{escape(labels[hint_key])}</span></div>'
        if audio_filename is not None
        else ""
    )
    lookup = (
        f'<div class="lookup"><span>{escape(labels["lookup_label"])}</span>'
        f'<input id="lookup" placeholder='
        f'"{escape(labels["lookup_placeholder"], quote=True)}">'
        f'<button id="lookup-go">{escape(labels["lookup_button"])}</button>'
        f'<span id="lookup-result" class="res"></span></div>'
        if report.seek_available
        else ""
    )
    legend = "".join(
        f'<span><i style="background:{colours.get(label, _SPEAKER_PALETTE[-1])}">'
        f"</i>{escape(name)}</span>"
        for label, name in report.speakers
    )
    return _PAGE.format(
        title=escape(f"{labels['report_title']} · {episode.title}"),
        heading=escape(labels["report_title"]),
        episode_line=escape(
            f"{episode.source_show} — {episode.source_title} · "
            f"{episode.guid} · {labels['run_label']} #{episode.run_id}"
        ),
        meta_line=escape(
            f"{labels['generated_at']} {episode.generated_at} · "
            f"{labels['source_duration']} "
            f"{report.summary.source_duration_seconds:.0f}s → "
            f"{labels['publication_duration']} "
            f"{episode.publication_duration_seconds}s"
        ),
        provenance_line=escape(
            f"{labels['models_label']}: {provenance.pipeline_version} · "
            f"{diarization_identity} · "
            f"{provenance.transcription_model} · "
            f"{provenance.alignment_model} · "
            f"{provenance.translation_provider}/{provenance.translation_model}"
        ),
        tiles=_summary_tiles(report, labels),
        notice=notice,
        player=player,
        lookup=lookup,
        timeline_label=escape(labels["timeline_label"]),
        timeline=_timeline(report, colours, labels),
        timeline_hint=escape(labels["timeline_suspect_hint"]),
        legend=legend,
        filters=_filter_chips(report, labels),
        segments="".join(
            _segment_block(
                segment,
                grouped.get(segment.segment_id, []),
                labels,
                colours,
            )
            for segment in report.segments
        ),
        export=escape(labels["export_button"]),
        export_hint=escape(labels["export_hint"]),
        lookup_miss=json.dumps(labels["lookup_miss"], ensure_ascii=False),
        payload=_embedded_json(report_payload(report)),
    )


def render_index_html(
    reports: list[tuple[EvaluationReport, str]],
    *,
    locale: str = DEFAULT_LOCALE,
) -> str:
    """Render the cross-episode overview linking every generated report."""
    labels = load_locale(locale)
    rows = "".join(
        "<tr>"
        f'<td><a href="{escape(href, quote=True)}">'
        f"{escape(report.episode.title)}</a></td>"
        f"<td>{escape(report.episode.source_show)}</td>"
        f"<td>#{report.episode.run_id}</td>"
        f"<td>{report.summary.segment_count}</td>"
        f"<td>{report.summary.crosstalk_count}</td>"
        f"<td>{report.summary.suspect_count}</td>"
        f"<td>{report.summary.reviewed_count} / {len(report.findings)}</td>"
        "</tr>"
        for report, href in reports
    )
    headers = "".join(
        f"<th>{escape(labels[key])}</th>"
        for key in (
            "index_episode",
            "index_source",
            "run_label",
            "summary_segments",
            "summary_crosstalk",
            "index_suspects",
            "index_reviewed",
        )
    )
    return _INDEX.format(
        title=escape(labels["index_title"]),
        headers=headers,
        rows=rows,
    )


_INDEX = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;
  background:#f4f5f7; color:#1a202c; padding:32px; }}
h1 {{ font-size:20px; margin-bottom:16px; }}
table {{ border-collapse:collapse; background:#fff; border-radius:10px;
  overflow:hidden; box-shadow:0 1px 2px rgba(15,23,42,.06); }}
th, td {{ padding:9px 14px; font-size:13px; text-align:left;
  border-bottom:1px solid #e2e8f0; }}
th {{ background:#f8fafc; font-size:11px; color:#64748b; letter-spacing:.05em; }}
a {{ color:#0f766e; }}
</style>
</head>
<body>
<h1>{title}</h1>
<table><tr>{headers}</tr>{rows}</table>
</body>
</html>
"""


_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{
  --bg:#f4f5f7; --card:#fff; --ink:#1a202c; --muted:#64748b; --line:#e2e8f0;
  --accent:#0f766e; --sus:#be123c; --sus-bg:#fff1f2; --xt:#7c3aed;
  --xt-bg:#f5f3ff;
}}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{
  font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Segoe UI",sans-serif;
  background:var(--bg); color:var(--ink); padding:28px 22px 60px; line-height:1.5;
}}
.wrap {{ max-width:1080px; margin:0 auto; }}
h1 {{ font-size:20px; }}
.ep {{ font-size:13.5px; color:var(--muted); margin-top:3px; }}
.meta {{ font-size:11.5px; color:var(--muted); margin-top:6px;
  font-family:ui-monospace,Menlo,monospace; }}
.tiles {{ display:grid; grid-template-columns:repeat(5,1fr); gap:10px;
  margin-top:16px; }}
.tile {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:9px 12px; }}
.tile .v {{ font-size:17px; font-weight:700; }}
.tile .k {{ font-size:10.5px; color:var(--muted); margin-top:1px; }}
.notice {{ margin-top:12px; background:#fffbeb; border:1px solid #fde68a;
  color:#92400e; border-radius:10px; padding:9px 14px; font-size:12.5px; }}
.player {{ display:flex; align-items:center; gap:12px; margin-top:14px;
  background:#1e293b; border-radius:12px; padding:9px 14px; }}
.player audio {{ flex:1; height:32px; }}
.player .hint {{ font-size:10.5px; color:#94a3b8; }}
.lookup {{ display:flex; align-items:center; gap:8px; margin-top:12px;
  font-size:12.5px; }}
.lookup input {{ width:100px; border:1px solid var(--line); border-radius:8px;
  padding:5px 10px; font-family:ui-monospace,Menlo,monospace; }}
.lookup button, .fchip, .vb {{ cursor:pointer; font-family:inherit; }}
.lookup button {{ background:var(--ink); color:#fff; border:none; border-radius:8px;
  padding:5px 14px; font-size:12px; }}
.res {{ color:var(--muted); }}
.tl-label {{ margin-top:18px; font-size:10.5px; font-weight:700;
  letter-spacing:.06em; color:#94a3b8; }}
.timeline {{ display:flex; gap:2px; margin-top:7px; height:32px; border-radius:8px;
  overflow:hidden; }}
.timeline div {{ display:flex; align-items:center; justify-content:center;
  cursor:pointer; font-size:11px; color:#fff; min-width:3px; }}
.events {{ margin-top:7px; font-size:11px; color:var(--muted); }}
.events b {{ margin-right:6px; }}
.events .event {{ display:inline-block; background:#f1f5f9; border-radius:6px;
  padding:1px 8px; margin-right:5px; font-family:ui-monospace,Menlo,monospace; }}
.legend {{ display:flex; flex-wrap:wrap; gap:12px; margin-top:8px; font-size:11.5px;
  color:var(--muted); }}
.legend i {{ display:inline-block; width:9px; height:9px; border-radius:3px;
  margin-right:4px; }}
.filters {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:16px; }}
.fchip {{ font-size:11.5px; border:1px solid var(--line); border-radius:999px;
  padding:3px 12px; color:var(--muted); background:#fff; }}
.fchip.on {{ border-color:var(--sus); color:var(--sus); background:var(--sus-bg);
  font-weight:600; }}
.seg {{ border:1px solid var(--line); border-radius:12px; padding:11px 14px;
  margin-top:10px; background:#fff; cursor:pointer; }}
.seg.suspect {{ border-color:#fecdd3; background:#fffafb; }}
.seg.hidden {{ display:none; }}
.seg.active {{ box-shadow:0 0 0 2px var(--accent); }}
.seg-top {{ display:flex; flex-wrap:wrap; align-items:center; gap:7px;
  font-size:11.5px; }}
.seg-id {{ font-family:ui-monospace,Menlo,monospace; color:#94a3b8; }}
.spk {{ font-weight:700; font-size:12px; padding:1px 9px; border-radius:999px; }}
.prov {{ font-size:10px; color:#92400e; background:#fffbeb; border-radius:999px;
  padding:1px 7px; }}
.times {{ font-family:ui-monospace,Menlo,monospace; color:var(--muted);
  font-size:10.5px; }}
.flag {{ font-size:10px; font-weight:700; border-radius:999px; padding:1px 8px;
  color:#fff; background:var(--sus); }}
.flag.crosstalk {{ color:var(--xt); background:var(--xt-bg); }}
.texts {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:8px;
  font-size:12.5px; }}
.texts .en {{ color:#475569; }}
.texts .zh {{ color:var(--accent); }}
.texts .l {{ display:block; font-size:9.5px; font-weight:700; color:#cbd5e1;
  letter-spacing:.08em; }}
.evidence {{ margin-top:10px; border-top:1px dashed #fecdd3; padding-top:8px;
  font-size:11.5px; color:#9f1239; }}
.evidence .etitle {{ font-weight:700; }}
.evidence ul {{ list-style:none; margin-top:5px; }}
.evidence li {{ margin-top:6px; }}
.words {{ margin-top:7px; font-size:10.5px; color:#881337; }}
.words pre {{ background:#fdf2f4; border-radius:7px; padding:7px 10px;
  margin-top:4px; font-family:ui-monospace,Menlo,monospace; white-space:pre-wrap; }}
.verdict {{ display:flex; flex-wrap:wrap; align-items:center; gap:6px;
  margin-top:7px; font-size:11px; }}
.vlabel {{ color:var(--muted); }}
.vb {{ border:1px solid var(--line); border-radius:999px; padding:2px 10px;
  color:var(--muted); background:#fff; font-size:11px; }}
.vb.sel {{ border-color:var(--sus); background:var(--sus); color:#fff;
  font-weight:600; }}
.export {{ display:flex; align-items:center; justify-content:flex-end; gap:10px;
  margin-top:18px; }}
.export button {{ background:var(--ink); color:#fff; border:none; border-radius:9px;
  padding:7px 16px; font-size:12px; cursor:pointer; }}
.export .hint {{ font-size:11px; color:var(--muted); }}
@media (max-width:760px) {{
  .tiles {{ grid-template-columns:repeat(2,1fr); }}
  .texts {{ grid-template-columns:1fr; }}
}}
</style>
</head>
<body>
<div class="wrap">
  <h1>{heading}</h1>
  <p class="ep">{episode_line}</p>
  <p class="meta">{meta_line}</p>
  <p class="meta">{provenance_line}</p>
  <div class="tiles">{tiles}</div>
  {notice}
  {player}
  {lookup}
  <div class="tl-label">{timeline_label}</div>
  {timeline}
  <div class="legend">{legend}<span>{timeline_hint}</span></div>
  <div class="filters">{filters}</div>
  <div id="segments">{segments}</div>
  <div class="export">
    <span class="hint">{export_hint}</span>
    <button id="export">{export}</button>
  </div>
</div>
<script type="application/json" id="report-data">
{payload}
</script>
<script>
(function () {{
  var data = JSON.parse(
    document.getElementById("report-data").textContent.replace(/<\\\\\\//g, "</")
  );
  var verdicts = {{}};
  data.findings.forEach(function (finding) {{
    if (finding.verdict) {{ verdicts[finding.key] = finding.verdict; }}
  }});
  var player = document.getElementById("player");
  var blocks = Array.prototype.slice.call(
    document.querySelectorAll("#segments .seg")
  );

  function focus(block) {{
    blocks.forEach(function (item) {{ item.classList.remove("active"); }});
    if (!block) {{ return; }}
    block.classList.add("active");
    block.scrollIntoView({{ block: "center", behavior: "smooth" }});
    var seek = block.getAttribute("data-seek");
    if (player && seek !== null) {{ player.currentTime = parseFloat(seek); }}
  }}

  blocks.forEach(function (block) {{
    block.addEventListener("click", function (event) {{
      if (event.target.closest(".vb")) {{ return; }}
      focus(block);
    }});
  }});

  document.querySelectorAll(".timeline div[data-segment]").forEach(function (cell) {{
    cell.addEventListener("click", function () {{
      focus(document.querySelector(
        '.seg[data-segment="' + cell.getAttribute("data-segment") + '"]'
      ));
    }});
  }});

  document.querySelectorAll(".vb").forEach(function (button) {{
    button.addEventListener("click", function () {{
      var key = button.getAttribute("data-key");
      var value = button.getAttribute("data-verdict");
      var selected = verdicts[key] === value;
      button.parentNode.querySelectorAll(".vb").forEach(function (peer) {{
        peer.classList.remove("sel");
      }});
      if (selected) {{
        delete verdicts[key];
      }} else {{
        verdicts[key] = value;
        button.classList.add("sel");
      }}
      var block = button.closest(".seg");
      block.setAttribute(
        "data-reviewed",
        block.querySelectorAll(".vb.sel").length > 0 ? "true" : "false"
      );
    }});
  }});

  document.querySelectorAll(".fchip").forEach(function (chip) {{
    chip.addEventListener("click", function () {{
      document.querySelectorAll(".fchip").forEach(function (peer) {{
        peer.classList.remove("on");
      }});
      chip.classList.add("on");
      var filter = chip.getAttribute("data-filter");
      blocks.forEach(function (block) {{
        var kinds = (block.getAttribute("data-kinds") || "").split(",");
        var reviewed = block.getAttribute("data-reviewed") === "true";
        var visible =
          filter === "all" ||
          (filter === "reviewed" && reviewed) ||
          (filter === "unreviewed" && kinds[0] !== "" && !reviewed) ||
          kinds.indexOf(filter) !== -1;
        block.classList.toggle("hidden", !visible);
      }});
    }});
  }});

  var lookup = document.getElementById("lookup-go");
  if (lookup) {{
    lookup.addEventListener("click", function () {{
      var raw = document.getElementById("lookup").value.trim();
      var parts = raw.split(":");
      var seconds = parts.length > 1
        ? parseFloat(parts[0]) * 60 + parseFloat(parts[1])
        : parseFloat(raw);
      var result = document.getElementById("lookup-result");
      if (isNaN(seconds)) {{ result.textContent = {lookup_miss}; return; }}
      var match = data.segments.filter(function (segment) {{
        return (
          segment.publication_start !== null &&
          segment.publication_start <= seconds &&
          segment.publication_end >= seconds
        );
      }})[0];
      if (!match) {{ result.textContent = {lookup_miss}; return; }}
      result.textContent = match.segment_id;
      focus(document.querySelector(
        '.seg[data-segment="' + match.segment_id + '"]'
      ));
    }});
  }}

  document.getElementById("export").addEventListener("click", function () {{
    var blob = new Blob(
      [JSON.stringify({{
        episode_guid: data.episode.guid,
        run_id: data.episode.run_id,
        verdicts: verdicts
      }}, null, 2)],
      {{ type: "application/json" }}
    );
    var link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "verdicts-" + data.episode.guid + ".json";
    link.click();
    URL.revokeObjectURL(link.href);
  }});
}})();
</script>
</body>
</html>
"""
