import {
  AlertCircle,
  ArrowLeft,
  Check,
  ChevronRight,
  Circle,
  Clock3,
  Copy,
  RefreshCw,
  RotateCcw
} from "lucide-react";
import { useCallback, useState } from "react";

import { ListeningAuditPanel } from "../components/ListeningAudit";
import { Badge, Button, Card } from "../components/ui";
import { runCopy } from "../i18n/runs";
import {
  api,
  asListeningAudit,
  type ApiRun,
  type ApiRunAttempt,
  type ApiRunSummary
} from "../lib/api";
import { useStableResource } from "../lib/useStableResource";
import { cn } from "../lib/utils";
import { PageHeading } from "./LibraryScreens";
import "./run-screens.css";

export function Runs({
  runId,
  onOpenRun
}: {
  runId: number | null;
  onOpenRun: (runId: number | null) => void;
}) {
  if (runId !== null) {
    return <RunDetail runId={runId} onBack={() => onOpenRun(null)} />;
  }
  return <RunList onOpenRun={onOpenRun} />;
}

function RunList({
  onOpenRun
}: {
  onOpenRun: (runId: number) => void;
}) {
  const loadRuns = useCallback(
    (signal: AbortSignal) => api.runs(signal),
    []
  );
  const runsRevision = useCallback(
    (value: { revision?: string }) => value.revision,
    []
  );
  const continueListPolling = useCallback(
    (value: { items: ApiRunSummary[] } | null) =>
      value === null ||
      value.items.some(
        (run) =>
          !["action_required", "publication_ready"].includes(
            run.owner_state
          )
      ),
    []
  );
  const resource = useStableResource(loadRuns, {
    poll: true,
    getRevision: runsRevision,
    shouldPoll: continueListPolling
  });
  const runs = resource.data?.items ?? [];

  return (
    <>
      <PageHeading
        title={runCopy.title}
        description={runCopy.description}
        actions={
          <Button
            tone="secondary"
            onClick={() => void resource.refresh()}
            aria-label={runCopy.actions.refresh}
          >
            <RefreshCw
              className={cn(resource.refreshing && "spinning-icon")}
              size={14}
              aria-hidden="true"
            />
            {runCopy.actions.refresh}
          </Button>
        }
      />
      {resource.stale ? (
        <p className="run-stale-notice" role="status">
          {runCopy.states.stale}
        </p>
      ) : null}
      {resource.initialLoading ? (
        <Card className="run-empty-state">
          {runCopy.states.initialLoading}
        </Card>
      ) : runs.length === 0 ? (
        <Card className="run-empty-state">
          <strong>{runCopy.list.empty}</strong>
          <span>{runCopy.list.emptyBody}</span>
        </Card>
      ) : (
        <Card className="run-library-list">
          {runs.map((run) => (
            <button
              className="run-library-row"
              type="button"
              key={run.id}
              onClick={() => onOpenRun(run.id)}
              aria-label={`${runCopy.list.open}: ${run.title}`}
            >
              <RunStateIcon state={run.owner_state} />
              <span className="run-library-identity">
                <strong>{run.title}</strong>
                <small>
                  <span>{run.source_show}</span>
                  <span aria-hidden="true">·</span>
                  <span>{stepLabel(run.current_step)}</span>
                </small>
              </span>
              <StateBadge state={run.owner_state} />
              <span className="run-library-metrics">
                <strong>
                  ${run.estimated_cost_usd.toFixed(2)}
                </strong>
                <small>
                  {formatTokens(
                    run.translation.input_tokens +
                      run.translation.output_tokens
                  )}{" "}
                  {runCopy.list.tokens}
                </small>
              </span>
              <span className="run-library-time">
                <small>{runCopy.list.latestChange}</small>
                <time dateTime={run.latest_change_at}>
                  {formatDateTime(run.latest_change_at)}
                </time>
              </span>
              <ChevronRight size={16} aria-hidden="true" />
            </button>
          ))}
        </Card>
      )}
    </>
  );
}

function RunDetail({
  runId,
  onBack
}: {
  runId: number;
  onBack: () => void;
}) {
  const loadRun = useCallback(
    (signal: AbortSignal) => api.runDetail(runId, signal),
    [runId]
  );
  const runRevision = useCallback((value: ApiRun) => value.revision, []);
  const continueRunPolling = useCallback(
    (value: ApiRun | null) =>
      value === null ||
      !["action_required", "publication_ready"].includes(
        value.owner_state
      ),
    []
  );
  const resource = useStableResource(loadRun, {
    poll: true,
    getRevision: runRevision,
    shouldPoll: continueRunPolling,
    resourceKey: runId
  });
  const [historyOpen, setHistoryOpen] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [actionError, setActionError] = useState("");
  const [diagnosticsState, setDiagnosticsState] = useState<
    "idle" | "copying" | "copied" | "error"
  >("idle");
  const run = resource.data;

  if (resource.initialLoading || run === null) {
    return (
      <>
        <Button tone="ghost" onClick={onBack}>
          <ArrowLeft size={14} aria-hidden="true" />
          {runCopy.actions.back}
        </Button>
        <Card className="run-empty-state">
          {runCopy.states.initialLoading}
        </Card>
      </>
    );
  }

  const retry = () => {
    setRetrying(true);
    setActionError("");
    api
      .retryRun(run.id)
      .then(resource.replace)
      .catch((reason: unknown) =>
        setActionError(
          reason instanceof Error ? reason.message : String(reason)
        )
      )
      .finally(() => setRetrying(false));
  };
  const copyDiagnostics = () => {
    setDiagnosticsState("copying");
    api
      .runDiagnostics(run.id)
      .then((diagnostics) =>
        navigator.clipboard.writeText(
          JSON.stringify(diagnostics, null, 2)
        )
      )
      .then(() => setDiagnosticsState("copied"))
      .catch(() => setDiagnosticsState("error"));
  };

  return (
    <>
      <button className="run-back-link" type="button" onClick={onBack}>
        <ArrowLeft size={14} aria-hidden="true" />
        {runCopy.actions.back}
      </button>
      <PageHeading
        title={run.title}
        description={`${run.source_show} · ${run.pipeline_version}`}
        actions={<StateBadge state={run.owner_state} />}
      />
      {resource.stale ? (
        <p className="run-stale-notice" role="status">
          {runCopy.states.stale}
        </p>
      ) : null}
      <div className="run-detail-layout">
        <main className="run-pipeline" aria-label={runCopy.title}>
          {run.pipeline_steps.map((step, index) => (
            <article
              className={cn(
                "run-pipeline-step",
                `is-${step.state}`
              )}
              key={step.id}
            >
              <span className="run-step-rail" aria-hidden="true">
                <span className="run-step-dot">
                  {step.state === "completed" ? (
                    <Check size={13} />
                  ) : step.state === "active" ? (
                    <RefreshCw className="spinning-icon" size={13} />
                  ) : (
                    <Circle size={10} />
                  )}
                </span>
                {index < run.pipeline_steps.length - 1 ? (
                  <span className="run-step-line" />
                ) : null}
              </span>
              <div className="run-step-content">
                <header>
                  <div>
                    <h2>{stepLabel(step.id)}</h2>
                    <span>{stateLabel(step.state)}</span>
                  </div>
                  {step.duration_ms !== null ? (
                    <time>
                      {formatDuration(step.duration_ms)}
                    </time>
                  ) : null}
                </header>
                {step.progress ? (
                  <div className="run-step-progress">
                    <span>
                      {step.progress.completed}/{step.progress.total}{" "}
                      {runCopy.progress.targets}
                    </span>
                    <progress
                      max={step.progress.total}
                      value={step.progress.completed}
                    />
                  </div>
                ) : null}
                {!step.evidence_available &&
                ["translation_authoring", "translation_review"].includes(
                  step.id
                ) &&
                step.state === "completed" ? (
                  <p>{runCopy.progress.unavailable}</p>
                ) : null}
                {step.id === "source_analysis" ? (
                  <AnalysisFlow metrics={step.metrics} />
                ) : null}
                <StepMetrics metrics={step.metrics} />
                {step.id === "publication_audio_quality" ? (
                  <StepListeningAudit metrics={step.metrics} />
                ) : null}
                {step.id === "translation_authoring" &&
                run.translation_project ? (
                  <p>
                    {runCopy.progress.authorTurns(
                      run.translation_project.author_turns
                    )}{" "}
                    ·{" "}
                    {formatTokens(
                      run.translation.input_tokens +
                        run.translation.output_tokens
                    )}{" "}
                    {runCopy.list.tokens}
                  </p>
                ) : null}
                {step.id === "translation_review" &&
                run.translation_project ? (
                  <p>
                    {runCopy.progress.reviewerTurns(
                      run.translation_project.reviewer_turns
                    )}
                  </p>
                ) : null}
                {step.technical_evidence ? (
                  <details className="run-technical-details">
                    <summary>{runCopy.actions.technical}</summary>
                    <dl>
                      {step.technical_evidence.version ? (
                        <div>
                          <dt>Version</dt>
                          <dd>{step.technical_evidence.version}</dd>
                        </div>
                      ) : null}
                      {step.technical_evidence.provider ? (
                        <div>
                          <dt>Provider</dt>
                          <dd>{step.technical_evidence.provider}</dd>
                        </div>
                      ) : null}
                      {step.technical_evidence.model ? (
                        <div>
                          <dt>Model</dt>
                          <dd>
                            {Array.isArray(
                              step.technical_evidence.model
                            )
                              ? step.technical_evidence.model.join(" · ")
                              : step.technical_evidence.model}
                          </dd>
                        </div>
                      ) : null}
                      {step.technical_evidence.cli_version ? (
                        <div>
                          <dt>CLI</dt>
                          <dd>{step.technical_evidence.cli_version}</dd>
                        </div>
                      ) : null}
                      {step.technical_evidence.modal_account ? (
                        <div>
                          <dt>{runCopy.technical.modalAccount}</dt>
                          <dd>
                            <Badge tone="neutral">
                              {step.technical_evidence.modal_account}
                            </Badge>
                          </dd>
                        </div>
                      ) : null}
                      {step.technical_evidence.modal_route &&
                      step.technical_evidence.modal_route.length > 0 ? (
                        <div>
                          <dt>{runCopy.technical.modalRoute}</dt>
                          <dd>
                            {step.technical_evidence.modal_route.join(
                              " → "
                            )}
                          </dd>
                        </div>
                      ) : null}
                      {step.technical_evidence.parallel_operations ? (
                        <div>
                          <dt>{runCopy.technical.parallelOperations}</dt>
                          <dd>
                            {Object.entries(
                              step.technical_evidence.parallel_operations
                            )
                              .map(
                                ([name, seconds]) =>
                                  `${name}: ${seconds.toFixed(1)}s`
                              )
                              .join(" · ")}
                          </dd>
                        </div>
                      ) : null}
                      {!step.technical_evidence.modal_account &&
                      (step.id.startsWith("translation_") ||
                        step.id === "publication_audio_quality") ? (
                        <div>
                          <dt>{runCopy.technical.modalUsage}</dt>
                          <dd>{runCopy.technical.localOnly}</dd>
                        </div>
                      ) : null}
                    </dl>
                  </details>
                ) : null}
              </div>
            </article>
          ))}
        </main>

        <aside
          className="run-current-panel"
          aria-label={runCopy.currentPanel}
        >
          {run.current_error ? (
            <div role="alert">
              <Card className="run-error-card">
                <span className="run-error-icon">
                  <AlertCircle size={17} aria-hidden="true" />
                </span>
                <div>
                  <h2>{runCopy.error.title}</h2>
                  <p>{currentErrorSummary(run.current_error)}</p>
                  <dl>
                    <div>
                      <dt>{runCopy.error.retryPlan}</dt>
                      <dd>{stepLabel(run.retry_plan.resume_step)}</dd>
                    </div>
                    <div>
                      <dt>{runCopy.error.reused}</dt>
                      <dd>
                        {run.retry_plan.reused_steps
                          .map(stepLabel)
                          .join(" · ") || "—"}
                      </dd>
                    </div>
                  </dl>
                  <p className="run-retry-note">
                    {run.retry_plan.preserves_translation_targets
                      ? runCopy.error.preservedTargets
                      : runCopy.error.repeatedTargets}
                  </p>
                  <Button
                    disabled={retrying || !run.current_error.retryable}
                    onClick={retry}
                  >
                    <RotateCcw size={14} aria-hidden="true" />
                    {retrying
                      ? runCopy.actions.retrying
                      : run.current_error.kind === "listening_audit"
                        ? runCopy.actions.acknowledgeAudit
                        : runCopy.actions.retry}
                  </Button>
                  {actionError ? (
                    <p className="run-action-error">{actionError}</p>
                  ) : null}
                </div>
              </Card>
            </div>
          ) : (
            <Card className="run-summary-card">
              <RunStateIcon state={run.owner_state} />
              <div>
                <strong>{stateLabel(run.owner_state)}</strong>
                <span>{stepLabel(run.current_step)}</span>
              </div>
            </Card>
          )}

          <Card className="run-usage-card">
            <h2>{runCopy.timing.title}</h2>
            <span>
              {runCopy.list.queuedAt}：{formatDateTime(run.queued_at)}
            </span>
            <span>
              {runCopy.list.firstClaimedAt}：
              {run.first_claimed_at
                ? formatDateTime(run.first_claimed_at)
                : runCopy.list.notClaimed}
            </span>
          </Card>

          <Card className="run-usage-card">
            <h2>{runCopy.list.cost}</h2>
            <strong>${run.estimated_cost_usd.toFixed(2)}</strong>
            <span>
              {formatTokens(run.translation.input_tokens)}{" "}
              {runCopy.usage.input} ·{" "}
              {formatTokens(run.translation.output_tokens)}{" "}
              {runCopy.usage.output}
            </span>
          </Card>

          <Card className="run-history-card">
            <div className="run-history-toolbar">
              <button
                type="button"
                onClick={() => setHistoryOpen((value) => !value)}
                aria-expanded={historyOpen}
              >
                <Clock3 size={14} aria-hidden="true" />
                {runCopy.actions.history(run.attempt_history.length)}
                <ChevronRight
                  className={cn(historyOpen && "is-open")}
                  size={14}
                  aria-hidden="true"
                />
              </button>
              <Button
                tone="ghost"
                onClick={copyDiagnostics}
                disabled={diagnosticsState === "copying"}
              >
                {diagnosticsState === "copied" ? (
                  <Check size={14} aria-hidden="true" />
                ) : (
                  <Copy size={14} aria-hidden="true" />
                )}
                {diagnosticsState === "copying"
                  ? runCopy.actions.copyingDiagnostics
                  : diagnosticsState === "copied"
                    ? runCopy.actions.diagnosticsCopied
                    : runCopy.actions.copyDiagnostics}
              </Button>
            </div>
            {diagnosticsState === "error" ? (
              <p className="run-diagnostics-error" role="alert">
                {runCopy.actions.diagnosticsFailed}
              </p>
            ) : null}
            {historyOpen ? (
              run.attempt_history.length > 0 ? (
                <div className="run-history-list">
                  {run.attempt_history.map((attempt) => (
                    <AttemptRow key={attempt.id} attempt={attempt} />
                  ))}
                </div>
              ) : (
                <p className="run-history-empty">
                  {runCopy.history.empty}
                </p>
              )
            ) : null}
          </Card>
        </aside>
      </div>
    </>
  );
}

function AttemptRow({ attempt }: { attempt: ApiRunAttempt }) {
  return (
    <details className="run-history-attempt">
      <summary
        aria-label={runCopy.history.openAttempt(
          attempt.sequence,
          stepLabel(attempt.stage)
        )}
      >
        <span className="run-attempt-sequence">#{attempt.sequence}</span>
        <span className="run-attempt-identity">
          <strong>{stepLabel(attempt.stage)}</strong>
          <small>{formatDateTime(attempt.created_at)}</small>
        </span>
        <Badge
          tone={
            attempt.status === "completed"
              ? "success"
              : attempt.status === "failed"
                ? "danger"
                : "warning"
          }
        >
          {attempt.superseded
            ? runCopy.states.superseded
            : stateLabel(attempt.status)}
        </Badge>
        <ChevronRight
          className="run-attempt-chevron"
          size={14}
          aria-hidden="true"
        />
      </summary>
      <div className="run-attempt-details">
        <dl>
          <div>
            <dt>{runCopy.history.duration}</dt>
            <dd>{formatDuration(attempt.duration_ms)}</dd>
          </div>
          <div>
            <dt>{runCopy.history.startedAt}</dt>
            <dd>{formatDateTime(attempt.started_at)}</dd>
          </div>
          {attempt.completed_at ? (
            <div>
              <dt>{runCopy.history.completedAt}</dt>
              <dd>{formatDateTime(attempt.completed_at)}</dd>
            </div>
          ) : null}
          {attempt.timing_provenance !== "recorded" ? (
            <div>
              <dt>{runCopy.history.timingEvidence}</dt>
              <dd>{runCopy.history.legacyTiming}</dd>
            </div>
          ) : null}
          <div>
            <dt>{runCopy.history.checkpoint}</dt>
            <dd>{stepLabel(attempt.resume_checkpoint)}</dd>
          </div>
          {attempt.transfer_bytes > 0 ? (
            <div>
              <dt>{runCopy.history.transfer}</dt>
              <dd>
                {formatMetric("transfer_bytes", attempt.transfer_bytes)}
              </dd>
            </div>
          ) : null}
          {attempt.error_kind ? (
            <div>
              <dt>{runCopy.history.errorKind}</dt>
              <dd>{attempt.error_kind}</dd>
            </div>
          ) : null}
          {typeof attempt.routing.selected_profile === "string" ? (
            <div>
              <dt>{runCopy.history.modalAccount}</dt>
              <dd>{attempt.routing.selected_profile}</dd>
            </div>
          ) : null}
          {Array.isArray(attempt.routing.attempts) ? (
            <div>
              <dt>{runCopy.history.modalRoute}</dt>
              <dd>
                {attempt.routing.attempts
                  .map((item: unknown) =>
                    typeof item === "object" &&
                    item !== null &&
                    "profile" in item
                      ? String(item.profile)
                      : ""
                  )
                  .filter(Boolean)
                  .join(" → ")}
              </dd>
            </div>
          ) : null}
        </dl>
        {attempt.error_summary ? (
          <p className="run-attempt-error">{attempt.error_summary}</p>
        ) : null}
      </div>
    </details>
  );
}

function StepMetrics({ metrics }: { metrics: Record<string, unknown> }) {
  const entries = Object.entries(metrics).filter(
    ([key, value]) =>
      value !== null &&
      value !== undefined &&
      !ANALYSIS_FLOW_METRICS.has(key) &&
      key !== "listening_audit"
  );
  if (entries.length === 0) {
    return null;
  }
  return (
    <dl className="run-step-metrics">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>
            {runCopy.metrics[key as keyof typeof runCopy.metrics] ?? key}
          </dt>
          <dd>{formatMetric(key, value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function StepListeningAudit({
  metrics
}: {
  metrics: Record<string, unknown>;
}) {
  const audit = asListeningAudit(metrics.listening_audit);
  return audit ? <ListeningAuditPanel audit={audit} /> : null;
}

const ANALYSIS_FLOW_METRICS = new Set([
  "ownership_region_count",
  "ownership_changed_word_count",
  "ownership_disputed_region_count",
  "natural_segment_count",
  "fallback_boundary_count",
  "ownership_degraded"
]);

function AnalysisFlow({ metrics }: { metrics: Record<string, unknown> }) {
  const regions = metricNumber(metrics.ownership_region_count);
  const changedWords = metricNumber(metrics.ownership_changed_word_count);
  const disputedRegions = metricNumber(
    metrics.ownership_disputed_region_count
  );
  const segments = metricNumber(metrics.natural_segment_count);
  const fallbackBoundaries = metricNumber(metrics.fallback_boundary_count);
  if (
    regions === null ||
    changedWords === null ||
    disputedRegions === null ||
    segments === null ||
    fallbackBoundaries === null
  ) {
    return null;
  }
  return (
    <section
      className={cn(
        "run-analysis-flow",
        metrics.ownership_degraded === true && "is-degraded"
      )}
      aria-label={runCopy.analysisFlow.title}
    >
      <div className="run-analysis-flow-heading">
        <strong>{runCopy.analysisFlow.title}</strong>
        {metrics.ownership_degraded === true ? (
          <span>
            <AlertCircle size={12} aria-hidden="true" />
            {runCopy.analysisFlow.degraded}
          </span>
        ) : null}
      </div>
      <ol>
        <li>
          <span className="run-analysis-flow-index">1</span>
          <div>
            <strong>{runCopy.analysisFlow.wordTimeline}</strong>
            <small>{runCopy.analysisFlow.wordTimelineHint}</small>
          </div>
        </li>
        <li>
          <span className="run-analysis-flow-index">2</span>
          <div>
            <strong>{runCopy.analysisFlow.speakerOwnership}</strong>
            <small>
              {runCopy.analysisFlow.ownershipSummary(
                regions,
                changedWords,
                disputedRegions
              )}
            </small>
          </div>
        </li>
        <li>
          <span className="run-analysis-flow-index">3</span>
          <div>
            <strong>{runCopy.analysisFlow.naturalSegmentation}</strong>
            <small>
              {runCopy.analysisFlow.segmentationSummary(
                segments,
                fallbackBoundaries
              )}
            </small>
          </div>
        </li>
      </ol>
    </section>
  );
}

function metricNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function RunStateIcon({ state }: { state: string }) {
  return (
    <span className={cn("run-state-icon", `is-${state}`)}>
      {["completed", "publication_ready"].includes(state) ? (
        <Check size={16} aria-hidden="true" />
      ) : state === "action_required" ? (
        <AlertCircle size={16} aria-hidden="true" />
      ) : (
        <RefreshCw
          className={
            ["processing", "active"].includes(state)
              ? "spinning-icon"
              : undefined
          }
          size={16}
          aria-hidden="true"
        />
      )}
    </span>
  );
}

function StateBadge({ state }: { state: string }) {
  const tone =
    state === "action_required"
      ? "danger"
      : ["completed", "publication_ready"].includes(state)
        ? "success"
        : ["waiting_retry", "waiting_to_retry"].includes(state)
          ? "warning"
          : "primary";
  return <Badge tone={tone}>{stateLabel(state)}</Badge>;
}

function stepLabel(step: string) {
  return (
    runCopy.steps[step as keyof typeof runCopy.steps] ??
    step.replaceAll("_", " ")
  );
}

function stateLabel(state: string) {
  return (
    runCopy.states[state as keyof typeof runCopy.states] ??
    state.replaceAll("_", " ")
  );
}

function currentErrorSummary(error: ApiRun["current_error"]) {
  if (error === null) {
    return "";
  }
  return (
    runCopy.error.summaries[
      error.kind as keyof typeof runCopy.error.summaries
    ] ?? error.summary
  );
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

export function formatDuration(milliseconds: number) {
  const totalSeconds = Math.max(0, Math.round(milliseconds / 1000));
  if (totalSeconds < 60) {
    return `${(milliseconds / 1000).toFixed(1)}s`;
  }
  return `${Math.floor(totalSeconds / 60)}m ${totalSeconds % 60}s`;
}

function formatTokens(tokens: number) {
  return new Intl.NumberFormat("zh-CN", {
    notation: tokens >= 1000 ? "compact" : "standard",
    maximumFractionDigits: 1
  }).format(tokens);
}

function formatMetric(key: string, value: unknown) {
  if (typeof value === "boolean") {
    if (key === "boundary_artifacts" || key === "source_language_leakage") {
      return value ? runCopy.values.present : runCopy.values.absent;
    }
    return value ? runCopy.values.yes : runCopy.values.no;
  }
  if (typeof value === "number") {
    if (key.endsWith("_bytes")) {
      return `${(value / 1024 / 1024).toFixed(1)} MB`;
    }
    if (key === "estimated_cost_usd") {
      return `$${value.toFixed(2)}`;
    }
    if (key === "sample_rate_hz") {
      return `${(value / 1000).toFixed(0)} kHz`;
    }
    if (key === "bitrate_kbps") {
      return `${value} kbps`;
    }
    if (key === "loudness_lufs") {
      return `${value.toFixed(1)} LUFS`;
    }
    if (key === "true_peak_dbtp") {
      return `${value.toFixed(1)} dBTP`;
    }
    return new Intl.NumberFormat("zh-CN").format(value);
  }
  if (typeof value === "object" && value !== null) {
    const seconds = Object.values(value).reduce(
      (total, item) =>
        total + (typeof item === "number" ? item : 0),
      0
    );
    return `${seconds.toFixed(1)}s`;
  }
  return String(value);
}
