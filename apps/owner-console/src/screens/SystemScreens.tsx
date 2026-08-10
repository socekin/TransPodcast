import { Checkbox } from "@base-ui/react/checkbox";
import {
  Check,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Copy,
  Pencil,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldCheck,
  UserRoundCheck
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";

import {
  Badge,
  Button,
  Card,
  AsyncResourceState,
  ServiceStatus
} from "../components/ui";
import { copy } from "../i18n/zh-CN";
import { api, type ApiSettings } from "../lib/api";
import { useApiResource } from "../lib/useApiResource";
import { PageHeading } from "./LibraryScreens";

export function Interests() {
  const [editing, setEditing] = useState(false);
  const [markdown, setMarkdown] = useState("");
  const [savedMarkdown, setSavedMarkdown] = useState("");
  const [notice, setNotice] = useState("");
  const loadInterest = useCallback(
    (signal: AbortSignal) => api.interest(signal),
    []
  );
  const resource = useApiResource(loadInterest);
  useEffect(() => {
    if (resource.data?.markdown) {
      setMarkdown(resource.data.markdown);
      setSavedMarkdown(resource.data.markdown);
    }
  }, [resource.data?.markdown]);

  return (
    <>
      <PageHeading
        title={copy.interests.title}
        description={copy.interests.description}
        actions={
          !editing && resource.status === "ready" ? (
            <Button onClick={() => setEditing(true)}>
              <Pencil size={14} aria-hidden="true" />
              {copy.common.edit}
            </Button>
          ) : null
        }
      />
      <Card className="interest-card">
        {resource.status === "loading" ? (
          <AsyncResourceState
            kind="loading"
            className="empty-state"
            icon={<RefreshCw size={18} aria-hidden="true" />}
            title={copy.interests.loadingTitle}
          />
        ) : resource.status === "error" ? (
          <AsyncResourceState
            kind="error"
            className="empty-state"
            title={copy.interests.loadErrorTitle}
            detail={resource.error}
            onRetry={resource.reload}
            retryLabel={copy.common.retry}
          />
        ) : editing ? (
          <>
            <label className="markdown-editor">
              <span>{copy.interests.editTitle}</span>
              <textarea
                value={markdown}
                onChange={(event) => setMarkdown(event.target.value)}
              />
            </label>
            <footer className="form-actions">
              <Button
                tone="ghost"
                onClick={() => {
                  setMarkdown(savedMarkdown);
                  setEditing(false);
                }}
              >
                {copy.common.cancel}
              </Button>
              <Button
                onClick={() =>
                  api
                    .updateInterest(markdown)
                    .then((saved) => {
                      setMarkdown(saved.markdown);
                      setSavedMarkdown(saved.markdown);
                      setNotice(
                        `${copy.interests.savedPrefix} v${saved.version}`
                      );
                      setEditing(false);
                      resource.reload();
                    })
                    .catch((reason: unknown) =>
                      setNotice(
                        reason instanceof Error
                          ? reason.message
                          : String(reason)
                      )
                    )
                }
              >
                <Save size={14} aria-hidden="true" />
                {copy.common.save}
              </Button>
            </footer>
          </>
        ) : (
          <MarkdownPreview markdown={markdown} />
        )}
        {notice ? <p className="success-notice">{notice}</p> : null}
      </Card>
    </>
  );
}

function MarkdownPreview({ markdown }: { markdown: string }) {
  return (
    <article className="markdown-preview">
      <ReactMarkdown
        skipHtml
        components={{
          a: ({ children, ...props }) => (
            <a {...props} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          )
        }}
      >
        {markdown}
      </ReactMarkdown>
    </article>
  );
}

type RunAttempt = Awaited<
  ReturnType<typeof api.runDetail>
>["attempts"][number];

export function Runs() {
  const [notice, setNotice] = useState("");
  const [openRunId, setOpenRunId] = useState<number | null>(null);
  const [attempts, setAttempts] = useState<RunAttempt[]>([]);
  const loadRuns = useCallback(
    (signal: AbortSignal) => api.runs(signal),
    []
  );
  const resource = useApiResource(loadRuns);
  const runs = resource.status === "ready" ? resource.data?.items ?? [] : null;
  const hasActiveRun = (runs ?? []).some(
    (run) => !["completed", "review_queue"].includes(run.status)
  );
  const loadAttempts = useCallback((runId: number) => {
    api
      .runDetail(runId)
      .then((detail) => setAttempts(detail.attempts))
      .catch(() => setAttempts([]));
  }, []);
  useEffect(() => {
    if (!hasActiveRun) {
      return;
    }
    const timer = window.setInterval(() => {
      resource.reload();
      if (openRunId !== null) {
        loadAttempts(openRunId);
      }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [hasActiveRun, loadAttempts, openRunId, resource]);
  return (
    <>
      <PageHeading title={copy.runs.title} description={copy.runs.description} />
      {notice ? <p className="inline-notice">{notice}</p> : null}
      <Card className="run-list">
        {runs?.length === 0 ? (
          <div className="empty-state">
            <CheckCircle2 size={20} aria-hidden="true" />
            <strong>{copy.runs.emptyTitle}</strong>
            <span>{copy.runs.emptyBody}</span>
          </div>
        ) : null}
        {runs?.map((run) => (
          <article
            className="run-row"
            key={run.id}
            onClick={() => {
              const next = openRunId === run.id ? null : run.id;
              setOpenRunId(next);
              if (next !== null) {
                loadAttempts(next);
              }
            }}
          >
            <span
              className={
                run.status === "completed"
                  ? "run-icon is-complete"
                  : "run-icon"
              }
            >
              {run.status === "completed" ? (
                <CheckCircle2 size={17} aria-hidden="true" />
              ) : (
                <RefreshCw size={17} aria-hidden="true" />
              )}
            </span>
            <span className="run-copy">
              <strong>
                {copy.runs.runPrefix} #{run.id}
              </strong>
              <small>
                {run.current_step.replaceAll("_", " ")} · {run.status.replaceAll("_", " ")}
              </small>
            </span>
            <Badge tone={run.status === "completed" ? "success" : "primary"}>
              {run.status === "completed"
                ? copy.common.ready
                : copy.common.processing}
            </Badge>
            <span className="run-cost">
              ${run.estimated_cost_usd.toFixed(2)}
            </span>
            {run.status === "review_queue" ? (
              <Button
                tone="secondary"
                size="sm"
                onClick={() =>
                  api
                    .retryRun(run.id)
                    .then(() => {
                      setNotice(copy.runs.retryQueued);
                      resource.reload();
                    })
                    .catch((reason: unknown) =>
                      setNotice(
                        reason instanceof Error
                          ? reason.message
                          : String(reason)
                      )
                    )
                }
              >
                {copy.runs.retry}
              </Button>
            ) : (
              <ChevronRight size={16} aria-hidden="true" />
            )}
            {openRunId === run.id && attempts.length > 0 ? (
              <div className="run-attempts">
                {attempts.map((attempt, index) => (
                  <p key={index}>
                    <Badge
                      tone={
                        attempt.status === "completed"
                          ? "success"
                          : attempt.status === "failed"
                            ? "danger"
                            : "warning"
                      }
                    >
                      {attempt.status}
                    </Badge>
                    <strong>
                      {attempt.stage.replaceAll("_", " ")}
                    </strong>
                    <span>
                      {(attempt.duration_ms / 1000).toFixed(1)}s
                      {attempt.transfer_bytes > 0
                        ? ` · ${(attempt.transfer_bytes / 1_000_000).toFixed(1)} MB`
                        : ""}
                    </span>
                    {attempt.error_summary ? (
                      <small>{attempt.error_summary}</small>
                    ) : null}
                  </p>
                ))}
              </div>
            ) : null}
          </article>
        ))}
        {runs === null ? (
          <>
        <article className="run-row">
          <span className="run-icon is-complete">
            <CheckCircle2 size={17} aria-hidden="true" />
          </span>
          <span className="run-copy">
            <strong>{copy.runs.runOne}</strong>
            <small>{copy.runs.completed}</small>
          </span>
          <Badge tone="success">{copy.common.ready}</Badge>
          <span className="run-cost">{copy.runs.estimate}</span>
          <ChevronRight size={16} aria-hidden="true" />
        </article>
        <article className="run-row">
          <span className="run-icon">
            <RefreshCw size={17} aria-hidden="true" />
          </span>
          <span className="run-copy">
            <strong>{copy.runs.runTwo}</strong>
            <small>{copy.runs.rendering}</small>
          </span>
          <Badge tone="primary">{copy.common.processing}</Badge>
          <span className="run-cost">{copy.runs.retryable}</span>
          <ChevronRight size={16} aria-hidden="true" />
        </article>
          </>
        ) : null}
      </Card>
    </>
  );
}

export function SettingsScreen() {
  const [notice, setNotice] = useState<{
    message: string;
    tone: "success" | "error";
  } | null>(null);
  const [saving, setSaving] = useState(false);
  const loadReadiness = useCallback(
    (signal: AbortSignal) => api.readiness(signal),
    []
  );
  const loadSettings = useCallback(
    (signal: AbortSignal) => api.settings(signal),
    []
  );
  const readiness = useApiResource(loadReadiness);
  const settings = useApiResource<ApiSettings | null>(loadSettings);
  const [automation, setAutomation] = useState({
    discovery_interval_minutes: 120,
    duration_guardrail_minutes: 180,
    processing_target_hours: 24,
    manual_processing: true,
    auto_discovery: false
  });
  useEffect(() => {
    if (settings.data?.automation) {
      setAutomation((current) => ({
        ...current,
        ...settings.data?.automation
      }));
    }
  }, [settings.data?.automation]);
  const automationFields: Array<{
    key: "discovery_interval_minutes"
      | "duration_guardrail_minutes"
      | "processing_target_hours";
    label: string;
    note: string;
    unit: string;
    min: number;
    value: number;
  }> = [
    {
      key: "discovery_interval_minutes",
      label: copy.settings.discoveryInterval,
      note: copy.settings.discoveryIntervalNote,
      unit: copy.settings.minutes,
      min: 15,
      value: automation.discovery_interval_minutes
    },
    {
      key: "duration_guardrail_minutes",
      label: copy.settings.durationGuardrail,
      note: copy.settings.durationGuardrailNote,
      unit: copy.settings.minutes,
      min: 15,
      value: automation.duration_guardrail_minutes
    },
    {
      key: "processing_target_hours",
      label: copy.settings.targetHours,
      note: copy.settings.targetHoursNote,
      unit: copy.settings.hours,
      min: 1,
      value: automation.processing_target_hours
    }
  ];

  const showError = (reason: unknown) =>
    setNotice({
      message: reason instanceof Error ? reason.message : String(reason),
      tone: "error"
    });

  if (settings.status === "loading" || readiness.status === "loading") {
    return (
      <>
        <PageHeading
          title={copy.settings.title}
          description={copy.settings.description}
        />
        <AsyncResourceState
          kind="loading"
          surface="card"
          className="empty-state"
          icon={<RefreshCw size={18} aria-hidden="true" />}
          title={copy.settings.loadingTitle}
        />
      </>
    );
  }

  if (settings.status === "error" || readiness.status === "error") {
    return (
      <>
        <PageHeading
          title={copy.settings.title}
          description={copy.settings.description}
        />
        <AsyncResourceState
          kind="error"
          surface="card"
          className="empty-state"
          title={copy.settings.loadErrorTitle}
          detail={settings.error || readiness.error}
          onRetry={() => {
            settings.reload();
            readiness.reload();
          }}
          retryLabel={copy.common.retry}
        />
      </>
    );
  }

  return (
    <>
      <PageHeading
        title={copy.settings.title}
        description={copy.settings.description}
      />
      <div className="settings-stack">
        {notice ? (
          <p
            className={
              notice.tone === "error"
                ? "error-notice settings-notice"
                : "success-notice settings-notice"
            }
            role="status"
          >
            {notice.message}
          </p>
        ) : null}

        <Card className="settings-panel settings-automation-card">
          <header className="settings-card-header">
            <span className="settings-card-icon">
              <Clock3 size={17} aria-hidden="true" />
            </span>
            <div className="settings-card-heading">
              <h2>{copy.settings.automationTitle}</h2>
              <p>{copy.settings.automationBody}</p>
            </div>
          </header>
          <div className="settings-field-list">
            {automationFields.map((field) => (
              <label className="setting-input-row" key={field.key}>
                <span>
                  <strong>{field.label}</strong>
                  <small>{field.note}</small>
                </span>
                <span className="setting-number-control">
                  <input
                    min={field.min}
                    type="number"
                    value={field.value}
                    onChange={(event) =>
                      setAutomation((current) => ({
                        ...current,
                        [field.key]: Number(event.target.value)
                      }))
                    }
                  />
                  <em>{field.unit}</em>
                </span>
              </label>
            ))}
            <label className="setting-input-row">
              <span>
                <strong>{copy.settings.autoDiscovery}</strong>
                <small>{copy.settings.autoDiscoveryNote}</small>
              </span>
              <Checkbox.Root
                className="checkbox-root setting-checkbox"
                checked={automation.auto_discovery}
                onCheckedChange={(checked) =>
                  setAutomation((current) => ({
                    ...current,
                    auto_discovery: checked === true
                  }))
                }
              >
                <Checkbox.Indicator>
                  <Check size={13} aria-hidden="true" />
                </Checkbox.Indicator>
              </Checkbox.Root>
            </label>
            <label className="setting-input-row">
              <span>
                <strong>{copy.settings.manualProcessing}</strong>
                <small>{copy.settings.manualProcessingNote}</small>
              </span>
              <Checkbox.Root
                className="checkbox-root setting-checkbox"
                checked={automation.manual_processing}
                onCheckedChange={(checked) =>
                  setAutomation((current) => ({
                    ...current,
                    manual_processing: checked === true
                  }))
                }
              >
                <Checkbox.Indicator>
                  <Check size={13} aria-hidden="true" />
                </Checkbox.Indicator>
              </Checkbox.Root>
            </label>
          </div>
          <footer className="settings-card-footer">
            <small>{copy.settings.saveNote}</small>
            <Button
              disabled={saving}
              onClick={() => {
                setSaving(true);
                setNotice(null);
                api
                  .updateSettings(automation)
                  .then((updated) => {
                    setAutomation(updated.automation);
                    setNotice({
                      message: copy.settings.saved,
                      tone: "success"
                    });
                    settings.reload();
                  })
                  .catch(showError)
                  .finally(() => setSaving(false));
              }}
            >
              <Save size={14} aria-hidden="true" />
              {saving ? copy.settings.saving : copy.common.save}
            </Button>
          </footer>
        </Card>

        <Card className="settings-panel feed-card">
          <header className="settings-card-header">
            <span className="settings-card-icon">
              <UserRoundCheck size={17} aria-hidden="true" />
            </span>
            <div className="settings-card-heading">
              <h2>{copy.settings.subscription}</h2>
              <p>{copy.settings.subscriptionBody}</p>
            </div>
          </header>
          <div className="feed-url">
            <code>
              {settings.data?.publication.subscription_url_masked ??
                copy.settings.masked}
            </code>
            <Button
              tone="secondary"
              size="sm"
              onClick={() => {
                setNotice(null);
                api
                  .privateFeedSubscription()
                  .then((value) =>
                    navigator.clipboard.writeText(value.subscription_url)
                  )
                  .then(() =>
                    setNotice({
                      message: copy.settings.copied,
                      tone: "success"
                    })
                  )
                  .catch(showError);
              }}
            >
              <Copy size={13} aria-hidden="true" />
              {copy.settings.copy}
            </Button>
            <Button
              size="sm"
              onClick={() => {
                setNotice(null);
                api
                  .privateFeedSubscription()
                  .then((value) => {
                    window.location.href = value.apple_podcasts_url;
                  })
                  .catch(showError);
              }}
            >
              {copy.settings.open}
            </Button>
          </div>
        </Card>

        <Card className="settings-panel readiness-card">
          <header className="settings-card-header">
            <span className="settings-card-icon">
              <ShieldCheck size={17} aria-hidden="true" />
            </span>
            <div className="settings-card-heading">
              <h2>{copy.settings.readinessTitle}</h2>
              <p>{copy.settings.readinessBody}</p>
            </div>
            <Button
              tone="secondary"
              onClick={readiness.reload}
            >
              <RotateCcw size={14} aria-hidden="true" />
              {copy.settings.recheck}
            </Button>
          </header>
          {readiness.data ? (
            <div className="service-list">
              <ServiceStatus
                title={copy.settings.codex}
                note={
                  readiness.data.details.codex?.impact ||
                  copy.settings.codexNote
                }
                configured={readiness.data.services.codex ?? false}
                readyLabel={copy.settings.ready}
                missingLabel={copy.settings.missing}
              />
              <ServiceStatus
                title={copy.settings.modal}
                note={
                  readiness.data.details.modal?.impact ||
                  copy.settings.modalNote
                }
                configured={readiness.data.services.modal ?? false}
                readyLabel={copy.settings.ready}
                missingLabel={copy.settings.missing}
              />
              <ServiceStatus
                title={copy.settings.cloudflare}
                note={
                  readiness.data.details.cloudflare?.impact ||
                  copy.settings.cloudflareNote
                }
                configured={readiness.data.services.cloudflare ?? false}
                readyLabel={copy.settings.ready}
                missingLabel={copy.settings.missing}
              />
              <ServiceStatus
                title={copy.settings.telegram}
                note={
                  readiness.data.details.telegram?.impact ||
                  copy.settings.telegramNote
                }
                configured={readiness.data.services.telegram ?? false}
                optional={readiness.data.details.telegram?.optional ?? true}
                readyLabel={copy.settings.ready}
                missingLabel={copy.settings.missing}
                optionalMissingLabel={copy.settings.optionalMissing}
              />
              <ServiceStatus
                title={copy.settings.feed}
                note={
                  readiness.data.details.private_feed?.impact ||
                  copy.settings.feedNote
                }
                configured={readiness.data.services.private_feed ?? false}
                readyLabel={copy.settings.ready}
                missingLabel={copy.settings.missing}
              />
            </div>
          ) : (
            <div className="settings-loading-state">
              {copy.settings.rechecking}
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
