import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";

import { ProgressRing, RingArt } from "../components/Art";
import {
  DiagnosticsButton,
  MixedHeading,
  PageHeader,
  SpeedMenu
} from "../components/Controls";
import { NextIcon, PauseIcon, PlayIcon, PreviousIcon } from "../components/Icons";
import { StepList } from "../components/StepList";
import { copy } from "../i18n/zh-CN";
import {
  api,
  type ApiLibraryItem,
  type ApiListeningAudit,
  type ApiListeningAuditFinding,
  type ApiRun,
  type ApiTranscriptLine,
  renderedAudioUrl
} from "../lib/api";
import { errorMessage, formatElapsed, formatLength } from "../lib/format";
import { useLibrary } from "../lib/library";
import { usePlayer } from "../lib/player";
import { useNavigate } from "../lib/routing";
import {
  currentStep,
  finishedSteps,
  runElapsedMs,
  stepAudit,
  stepLabel,
  stepTone
} from "../lib/runSteps";
import { useStableResource } from "../lib/useStableResource";
import { cn } from "../lib/utils";
import { findingDetail, findingKind, findingOrdinal } from "./findings";

const SETTLED = ["completed", "closed"];
const CLIP_LEAD_SECONDS = 0.3;
const CLIP_TAIL_SECONDS = 0.5;

type ProblemLine = {
  unitId: string;
  ordinal: number | null;
  findings: ApiListeningAuditFinding[];
  start: number;
  end: number;
};

function problemLines(audit: ApiListeningAudit): ProblemLine[] {
  return audit.blocking_unit_ids.map((unitId) => {
    const findings = audit.findings.filter(
      (finding) =>
        finding.unit_id === unitId || finding.parent_segment_id === unitId
    );
    const starts = findings
      .map((finding) => finding.output_start_seconds)
      .filter((value): value is number => value !== null);
    const ends = findings
      .map((finding) => finding.output_end_seconds)
      .filter((value): value is number => value !== null);
    return {
      unitId,
      ordinal: findings[0] ? findingOrdinal(findings[0]) : null,
      findings,
      start: starts.length ? Math.min(...starts) : 0,
      end: ends.length ? Math.max(...ends) : 0
    };
  });
}

export function RunPage({ id }: { id: number }) {
  const navigate = useNavigate();
  const library = useLibrary();
  const load = useCallback((signal: AbortSignal) => api.run(id, signal), [id]);
  const revision = useCallback((value: ApiRun) => value.revision, []);
  const keepPolling = useCallback(
    (value: ApiRun | null) => value === null || !SETTLED.includes(value.status),
    []
  );
  const resource = useStableResource(load, {
    poll: true,
    pollIntervalMs: 5000,
    getRevision: revision,
    shouldPoll: keepPolling,
    resourceKey: id
  });
  const run = resource.data;
  const item = library.items.find((entry) => entry.processing_run_id === id);
  const refreshLibrary = library.refresh;

  // A finished run belongs to its episode now.
  useEffect(() => {
    if (run?.status !== "completed") {
      return;
    }
    if (item?.episode_id) {
      navigate({ name: "episode", id: item.episode_id }, { replace: true });
    } else {
      void refreshLibrary();
    }
  }, [item?.episode_id, navigate, refreshLibrary, run?.status]);

  if (!run) {
    return (
      <div className="page detail-page">
        <PageHeader title={copy.run.processingTitle} />
        <p
          className="muted-paragraph page-status"
          role={resource.error ? "alert" : "status"}
        >
          {resource.error
            ? `${copy.run.loadError}：${resource.error}`
            : copy.run.loading}
        </p>
      </div>
    );
  }

  const audit =
    run.status === "review_queue" && run.current_error?.kind === "listening_audit"
      ? stepAudit(
          run.pipeline_steps.find((step) => step.id === "publication_audio_quality")
        )
      : null;
  const replace = (value: ApiRun) => {
    resource.replace(value);
    void library.refresh();
  };

  if (audit && audit.blocking_unit_ids.length) {
    return <AuditDecision run={run} audit={audit} item={item} onChange={replace} />;
  }
  return <RunProgress run={run} item={item} onChange={replace} />;
}

function runTitle(run: ApiRun): string {
  switch (run.status) {
    case "queued":
      return copy.run.queuedTitle;
    case "pending_start":
      return copy.run.readyToStartTitle;
    case "closed":
      return copy.run.closedTitle;
    case "review_queue":
    case "spend_guardrail":
    case "failed":
      return copy.run.decisionTitle;
    default:
      return copy.run.processingTitle;
  }
}

function StepsPanel({
  run,
  item,
  decision
}: {
  run: ApiRun;
  item?: ApiLibraryItem;
  decision?: ReactNode;
}) {
  const blockedIndex = run.pipeline_steps.findIndex(
    (step) => stepTone(step) === "blocked"
  );
  const hint =
    run.status === "closed"
      ? copy.run.stepsClosed
      : blockedIndex >= 0
        ? copy.run.stepsBlocked(blockedIndex, blockedIndex + 1)
        : copy.run.stepsHint;
  return (
    <section className="panel steps-panel" aria-labelledby="steps-title">
      <MixedHeading
        bold={copy.run.stepsBold}
        light={copy.run.stepsLight}
        id="steps-title"
        className="panel-title"
      />
      <p className="muted-paragraph">{hint}</p>
      <StepList
        run={run}
        sourceSeconds={item?.duration_seconds ?? 0}
        decision={decision}
      />
    </section>
  );
}

function useNow(intervalMs: number) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

function RunProgress({
  run,
  item,
  onChange
}: {
  run: ApiRun;
  item?: ApiLibraryItem;
  onChange: (run: ApiRun) => void;
}) {
  const now = useNow(30000);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const total = run.pipeline_steps.length;
  const done = finishedSteps(run);
  const step = currentStep(run);
  const partial =
    step?.progress && step.progress.total > 0
      ? step.progress.completed / step.progress.total
      : 0;
  const elapsed = runElapsedMs(run, now);
  const waiting = run.status === "queued" || run.status === "pending_start";
  const attention = ["review_queue", "spend_guardrail", "failed"].includes(
    run.status
  );
  const closed = run.status === "closed";

  let center: ReactNode;
  if (waiting) {
    center = (
      <>
        <span className="ring-center-label">
          {run.status === "queued" ? copy.run.queuedCenter : copy.run.readyToStartTitle}
        </span>
        <span className="muted-note">
          {run.status === "queued" ? copy.run.queuedNote : copy.run.readyToStartNote}
        </span>
      </>
    );
  } else if (closed || attention) {
    center = (
      <>
        <span className="ring-center-figure">!</span>
        <span className="ring-center-label">
          {closed ? copy.run.closedTitle : copy.run.decisionTitle}
        </span>
        {step ? <span className="muted-note">{stepLabel(step.id)}</span> : null}
      </>
    );
  } else {
    center = (
      <>
        <span className="ring-center-figure">
          {Math.min(done + 1, total)}
          <span className="ring-center-total"> / {total}</span>
        </span>
        <span className="ring-center-label">
          {step ? copy.stepActive[step.id] ?? stepLabel(step.id) : ""}
        </span>
        {step?.progress ? (
          <span className="muted-note">
            {copy.run.linesDone(step.progress.completed, step.progress.total)}
          </span>
        ) : null}
      </>
    );
  }

  return (
    <div className="page detail-page">
      <PageHeader title={runTitle(run)} />
      <div className="detail-grid">
        <section className="player-column" aria-label={copy.run.processingTitle}>
          <p className="player-clock">
            {waiting ? (
              copy.run.queuedTitle
            ) : elapsed !== null ? (
              <>
                {copy.run.elapsed} <span>{formatElapsed(elapsed)}</span>
              </>
            ) : null}
          </p>
          <ProgressRing
            fraction={(done + partial) / Math.max(total, 1)}
            label={copy.run.progressLabel(done, total)}
          >
            {center}
          </ProgressRing>
          <h2 className="player-title">{run.title}</h2>
          <p className="player-subtitle">
            {closed
              ? copy.run.closedSubtitle(run.source_show)
              : item
                ? copy.run.subtitle(run.source_show, formatLength(item.duration_seconds))
                : run.source_show}
          </p>
          {run.status === "pending_start" ? (
            <button
              type="button"
              className="pill-button is-primary is-tall"
              disabled={starting}
              onClick={() => {
                setStarting(true);
                setError("");
                api
                  .startRun(run.id)
                  .then(() => api.run(run.id))
                  .then(onChange)
                  .catch((reason: unknown) => setError(errorMessage(reason)))
                  .finally(() => setStarting(false));
              }}
            >
              {starting ? copy.actions.working : copy.run.startNow}
            </button>
          ) : null}
          {!closed && !attention ? (
            <p className="muted-note">{copy.run.closeAnytime}</p>
          ) : null}
          {error ? (
            <p className="panel-notice" role="alert">
              {error}
            </p>
          ) : null}
        </section>
        <StepsPanel
          run={run}
          item={item}
          decision={<FailureDecision run={run} onChange={onChange} />}
        />
      </div>
    </div>
  );
}

function useRunAction(onChange: (run: ApiRun) => void) {
  const [busy, setBusy] = useState<"retry" | "close" | null>(null);
  const [error, setError] = useState("");
  const perform = (kind: "retry" | "close", work: () => Promise<ApiRun>) => {
    setBusy(kind);
    setError("");
    work()
      .then(onChange)
      .catch((reason: unknown) => setError(errorMessage(reason)))
      .finally(() => setBusy(null));
  };
  return { busy, error, perform };
}

function FailureDecision({
  run,
  onChange
}: {
  run: ApiRun;
  onChange: (run: ApiRun) => void;
}) {
  const { busy, error, perform } = useRunAction(onChange);
  const failure = run.current_error;
  if (run.status === "closed") {
    return (
      <div className="decision-card">
        <p>{copy.run.stepsClosed}</p>
        {failure ? <p className="muted-note">{failure.summary}</p> : null}
        <DiagnosticsButton runId={run.id} />
      </div>
    );
  }
  const summary = failure
    ? copy.run.errors[failure.kind] ?? failure.summary
    : run.status === "spend_guardrail"
      ? copy.run.decision.spendGuardrail
      : "";
  const retryable = Boolean(failure?.retryable);
  return (
    <div className="decision-card">
      <p className="decision-title">{copy.run.decision.errorTitle}</p>
      {summary ? <p>{summary}</p> : null}
      {retryable ? (
        <p className="muted-note">
          {copy.run.decision.resumeFrom(stepLabel(run.retry_plan.resume_step))}
          {run.retry_plan.preserves_translation_targets
            ? ` · ${copy.run.decision.keepsTranslation}`
            : ""}
        </p>
      ) : (
        <p className="muted-note">{copy.run.decision.notRetryable}</p>
      )}
      <div className="decision-actions">
        {retryable ? (
          <button
            type="button"
            className="pill-button is-primary"
            disabled={busy !== null}
            onClick={() => perform("retry", () => api.retryRun(run.id))}
          >
            {busy === "retry" ? copy.actions.working : copy.run.decision.resume}
          </button>
        ) : null}
        {run.status === "review_queue" ? (
          <button
            type="button"
            className="pill-button is-outline"
            disabled={busy !== null}
            onClick={() => {
              if (window.confirm(copy.run.decision.closeConfirm)) {
                perform("close", () => api.closeRun(run.id));
              }
            }}
          >
            {busy === "close" ? copy.actions.working : copy.run.decision.close}
          </button>
        ) : null}
      </div>
      <DiagnosticsButton runId={run.id} />
      {error ? (
        <p className="panel-notice" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** Problem-line audition with its own speed, which starts at 1× so the
 * Owner judges pronunciation at the real pace. */
function useClip(src: string) {
  const player = usePlayer();
  const audio = useRef<HTMLAudioElement | null>(null);
  const window_ = useRef<{ start: number; end: number } | null>(null);
  const [playing, setPlaying] = useState(false);
  const [fraction, setFraction] = useState(0);
  const [rate, setRateState] = useState(1);
  const [unavailable, setUnavailable] = useState(false);
  const rateRef = useRef(1);
  const pausePlayer = player.pause;

  useEffect(() => {
    const element = new Audio();
    element.preload = "metadata";
    element.src = src;
    audio.current = element;
    const onTime = () => {
      const clip = window_.current;
      if (!clip) {
        return;
      }
      const length = Math.max(clip.end - clip.start, 0.1);
      setFraction(Math.min(1, Math.max(0, (element.currentTime - clip.start) / length)));
      if (element.currentTime >= clip.end) {
        element.pause();
      }
    };
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onError = () => {
      setUnavailable(true);
      setPlaying(false);
    };
    element.addEventListener("timeupdate", onTime);
    element.addEventListener("play", onPlay);
    element.addEventListener("pause", onPause);
    element.addEventListener("error", onError);
    return () => {
      element.pause();
      element.removeEventListener("timeupdate", onTime);
      element.removeEventListener("play", onPlay);
      element.removeEventListener("pause", onPause);
      element.removeEventListener("error", onError);
      audio.current = null;
    };
  }, [src]);

  const play = useCallback(
    (start: number, end: number) => {
      const element = audio.current;
      if (!element) {
        return;
      }
      window_.current = {
        start: Math.max(0, start - CLIP_LEAD_SECONDS),
        end: end + CLIP_TAIL_SECONDS
      };
      pausePlayer();
      element.currentTime = window_.current.start;
      element.playbackRate = rateRef.current;
      setFraction(0);
      void element.play().catch((reason: unknown) => {
        // A pause before playback starts is not a missing file.
        if (!(reason instanceof DOMException && reason.name === "AbortError")) {
          setUnavailable(true);
        }
      });
    },
    [pausePlayer]
  );
  const stop = useCallback(() => audio.current?.pause(), []);
  const setRate = useCallback((value: number) => {
    rateRef.current = value;
    setRateState(value);
    if (audio.current) {
      audio.current.playbackRate = value;
    }
  }, []);
  const reset = useCallback(() => {
    audio.current?.pause();
    window_.current = null;
    setFraction(0);
  }, []);
  return { playing, fraction, rate, unavailable, play, stop, setRate, reset };
}

function AuditDecision({
  run,
  audit,
  item,
  onChange
}: {
  run: ApiRun;
  audit: ApiListeningAudit;
  item?: ApiLibraryItem;
  onChange: (run: ApiRun) => void;
}) {
  const lines = useMemo(() => problemLines(audit), [audit]);
  const [selected, setSelected] = useState(0);
  const [transcript, setTranscript] = useState<ApiTranscriptLine[]>([]);
  const clip = useClip(renderedAudioUrl(run.id));
  const { busy, error, perform } = useRunAction(onChange);
  const line = lines[Math.min(selected, lines.length - 1)];
  const resetClip = clip.reset;

  useEffect(() => {
    const controller = new AbortController();
    api
      .transcript(run.id, controller.signal)
      .then((value) => setTranscript(value.lines))
      .catch(() => undefined);
    return () => controller.abort();
  }, [run.id]);

  useEffect(() => {
    resetClip();
    // A new line starts from its beginning.
  }, [selected, resetClip]);

  const target = transcript.find(
    (entry) => entry.segment_id === line.unitId || entry.ordinal === line.ordinal
  );
  const totalLines = transcript.length || audit.unit_count || 0;
  const select = (index: number) =>
    setSelected(Math.min(Math.max(index, 0), lines.length - 1));

  const decision = (
    <div className="decision-card">
      <p className="decision-title">
        {copy.run.decision.auditTitle(lines.length)}
      </p>
      {lines.length > 1 ? (
        <div className="problem-lines" role="group" aria-label={copy.run.decision.lines}>
          {lines.map((entry, index) => (
            <button
              key={entry.unitId}
              type="button"
              className={cn("chip is-small", index === selected && "is-selected")}
              aria-pressed={index === selected}
              onClick={() => select(index)}
            >
              {entry.ordinal !== null ? copy.quality.line(entry.ordinal) : entry.unitId}
            </button>
          ))}
        </div>
      ) : null}
      <ul className="problem-findings">
        {line.findings.map((finding, index) => (
          <li key={`${finding.kind}-${index}`}>
            <b>{findingKind(finding)}</b> · {findingDetail(finding)}
          </li>
        ))}
      </ul>
      {target ? (
        <p className="problem-target">
          <span className="muted-note">{copy.run.decision.targetText}</span>
          {target.target_text}
        </p>
      ) : null}
      <p className="muted-note">
        {audit.retry ? `${copy.run.decision.auditRetried} ` : ""}
        {copy.run.decision.auditListen}
      </p>
      <div className="decision-actions">
        <button
          type="button"
          className="pill-button is-primary"
          disabled={busy !== null}
          onClick={() => perform("retry", () => api.retryRun(run.id))}
        >
          {busy === "retry" ? copy.actions.working : copy.run.decision.acknowledge}
        </button>
        <button
          type="button"
          className="pill-button is-outline"
          disabled={busy !== null}
          onClick={() => {
            if (window.confirm(copy.run.decision.closeConfirm)) {
              perform("close", () => api.closeRun(run.id));
            }
          }}
        >
          {busy === "close" ? copy.actions.working : copy.run.decision.close}
        </button>
      </div>
      <DiagnosticsButton runId={run.id} />
      {error ? (
        <p className="panel-notice" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );

  return (
    <div className="page detail-page">
      <PageHeader title={copy.run.decisionTitle} />
      <div className="detail-grid is-decision">
        <section className="player-column" aria-label={copy.run.decision.lines}>
          <div className="player-meta">
            <p className="player-clock">
              {copy.run.linePrefix} <span>{line.ordinal ?? "—"}</span>
              {totalLines ? ` / ${totalLines}` : ""} {copy.run.lineSuffix}
            </p>
            <SpeedMenu
              rate={clip.rate}
              onChange={clip.setRate}
              note={copy.speed.clip}
            />
          </div>
          <RingArt src={item?.artwork_url} progress={clip.fraction} />
          <h2 className="player-title">{run.title}</h2>
          <p className="player-subtitle">
            {copy.run.decisionSubtitle(run.source_show)}
          </p>
          <div className="transport">
            <button
              type="button"
              className="circle-button is-transport"
              aria-label={copy.run.previousLine}
              disabled={selected === 0}
              onClick={() => select(selected - 1)}
            >
              <PreviousIcon size={18} />
            </button>
            <button
              type="button"
              className="play-button is-large"
              disabled={clip.unavailable}
              aria-label={
                clip.playing
                  ? copy.run.pauseLine
                  : copy.run.playLine(line.ordinal ?? selected + 1)
              }
              onClick={() =>
                clip.playing ? clip.stop() : clip.play(line.start, line.end)
              }
            >
              {clip.playing ? <PauseIcon size={26} /> : <PlayIcon size={26} />}
            </button>
            <button
              type="button"
              className="circle-button is-transport"
              aria-label={copy.run.nextLine}
              disabled={selected >= lines.length - 1}
              onClick={() => select(selected + 1)}
            >
              <NextIcon size={18} />
            </button>
          </div>
          {clip.unavailable ? (
            <p className="muted-note" role="status">
              {copy.run.noAudio}
            </p>
          ) : null}
        </section>
        <StepsPanel run={run} item={item} decision={decision} />
      </div>
    </div>
  );
}
