import { Checkbox } from "@base-ui/react/checkbox";
import { Radio } from "@base-ui/react/radio";
import { RadioGroup } from "@base-ui/react/radio-group";
import {
  Check,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  ExternalLink,
  Plus,
  Radio as RadioIcon,
  RotateCcw,
  Search,
  Sparkles,
  X
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import type { View } from "../components/Shell";
import {
  Badge,
  Button,
  Card,
  Field,
  IconButton,
  AsyncResourceState
} from "../components/ui";
import { copy } from "../i18n/zh-CN";
import {
  api,
  type ApiBackfillItem,
  type ApiCandidate,
  type ApiChannel,
  type ApiChannelDetail,
  type ApiChannelRecentVideo,
  type ApiSettings,
  type ApiSourceResolution
} from "../lib/api";
import {
  type ResourceState,
  useApiResource
} from "../lib/useApiResource";
import { DEFAULT_ARTWORK_URL } from "../lib/utils";
import { PageHeading } from "./LibraryScreens";

export function DecisionQueue({
  resource
}: {
  resource: ResourceState<{ items: ApiCandidate[] }>;
}) {
  const [decision, setDecision] = useState<"process" | "ignore" | null>(null);
  const [error, setError] = useState("");
  const [ignoredSearch, setIgnoredSearch] = useState("");
  const loadIgnored = useCallback(
    (signal: AbortSignal) => api.ignored(signal),
    []
  );
  const ignored = useApiResource(loadIgnored);
  const candidate =
    resource.status === "ready" ? resource.data?.items[0] : null;
  const topics = candidate
    ? parseTopics(candidate.matched_interests)
    : [];
  const decisionCount = resource.data?.items.length ?? 0;
  const decisionRationale = candidate
    ? candidate.recommendation_zh?.trim() ||
      formatDecisionRationale(candidate.rationale)
    : "";
  const decisionSummary = candidate
    ? candidate.summary_zh?.trim() ||
      summarizeCandidateDescription(candidate.description)
    : copy.decisions.summaryFallback;
  const decisionTitle = candidate
    ? candidate.title_zh?.trim() || candidate.title
    : "";
  const submitDecision = (value: "process" | "ignore") => {
    setError("");
    if (!candidate) {
      setDecision(value);
      return;
    }
    api
      .decide(candidate.id, value)
      .then(() => {
        setDecision(value);
        resource.reload();
      })
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : String(reason))
      );
  };

  return (
    <>
      <PageHeading
        title={copy.decisions.title}
        description={copy.decisions.description}
        actions={
          <Badge tone="warning">
            {resource.status === "ready"
              ? `${resource.data?.items.length ?? 0} ${copy.decisions.remainingUnit}`
              : copy.decisions.remaining}
          </Badge>
        }
      />

      {resource.status === "loading" ? (
        <AsyncResourceState
          kind="loading"
          surface="card"
          className="empty-state"
          icon={<RotateCcw size={20} aria-hidden="true" />}
          title={copy.decisions.loadingTitle}
        />
      ) : null}
      {resource.status === "error" ? (
        <AsyncResourceState
          kind="error"
          surface="card"
          className="empty-state"
          icon={<CircleAlert size={20} aria-hidden="true" />}
          title={copy.decisions.loadErrorTitle}
          detail={resource.error}
          onRetry={resource.reload}
          retryLabel={copy.common.retry}
        />
      ) : null}
      {resource.status === "ready" && !candidate ? (
        <Card className="empty-state">
          <CheckCircle2 size={20} aria-hidden="true" />
          <strong>{copy.decisions.emptyTitle}</strong>
          <span>{copy.decisions.emptyBody}</span>
        </Card>
      ) : null}
      {resource.status !== "ready" || !candidate ? null : (
        <Card className="decision-card">
          <div className="decision-media">
            <div className="decision-thumbnail">
              <img
                src={DEFAULT_ARTWORK_URL}
                alt={decisionTitle}
                onError={(event) => {
                  event.currentTarget.src =
                    "/assets/transpodcast-artwork.png";
                }}
              />
              <span className="decision-duration">
                {candidate
                  ? formatDuration(candidate.duration_seconds)
                  : "—"}
              </span>
              <button
                className="decision-source-link"
                type="button"
                onClick={() => {
                  if (candidate?.source_url) {
                    window.open(candidate.source_url, "_blank", "noopener");
                  }
                }}
              >
                <ExternalLink size={14} aria-hidden="true" />
                {copy.decisions.openYoutube}
              </button>
            </div>
            <div className="decision-review-context">
              <small>
                {copy.decisions.reviewProgress} 1 /{" "}
                {decisionCount || 1}
              </small>
              <strong>{copy.decisions.whyReview}</strong>
              <p>{copy.decisions.whyReviewBody}</p>
            </div>
          </div>

          <div className="decision-content">
            <header className="decision-header">
              <div>
                <Badge tone="warning">
                  <Sparkles size={12} aria-hidden="true" />
                  {copy.decisions.recommendation}
                </Badge>
                <Badge tone="neutral">
                  {candidate && candidate.confidence > 0
                    ? `${Math.round(candidate.confidence * 100)}% ${copy.decisions.confidenceSuffix}`
                    : copy.decisions.evidenceMissing}
                </Badge>
              </div>
            </header>
            <h1>{decisionTitle}</h1>
            <p className="decision-meta">
              {copy.decisions.youtubeCandidate} ·{" "}
              {candidate
                ? formatDuration(candidate.duration_seconds)
                : "—"}
            </p>

            <section className="decision-summary">
              <h2>{copy.decisions.contentPreview}</h2>
              <p>{decisionSummary}</p>
            </section>

            <div className="decision-facts">
              <section>
                <h2>
                  <CheckCircle2 size={15} aria-hidden="true" />
                  {copy.decisions.matches}
                </h2>
                {topics.length > 0 ? (
                  <div className="topic-tags">
                    {topics.map((topic) => (
                      <Badge tone="primary" key={topic}>
                        {topic}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <p>{copy.decisions.noMatches}</p>
                )}
              </section>
              <section>
                <h2>
                  <CircleAlert size={15} aria-hidden="true" />
                  {copy.decisions.agentJudgement}
                </h2>
                <p>{decisionRationale}</p>
              </section>
            </div>

            <footer className="decision-actions">
              <small>{copy.decisions.learned}</small>
              <div>
                <Button
                  tone="secondary"
                  onClick={() => submitDecision("ignore")}
                >
                  <X size={14} aria-hidden="true" />
                  {copy.decisions.ignore}
                </Button>
                <Button onClick={() => submitDecision("process")}>
                  <Check size={14} aria-hidden="true" />
                  {copy.decisions.process}
                </Button>
              </div>
            </footer>
            {decision ? (
              <p className="decision-result">
                {decision === "process"
                  ? copy.decisions.decidedProcess
                  : copy.decisions.decidedIgnore}
              </p>
            ) : null}
            {error ? <p className="error-notice">{error}</p> : null}
          </div>
        </Card>
      )}
      <details className="ignored-card">
        <summary>
          <span>
            <strong>{copy.decisions.ignoredTitle}</strong>
            <small>{copy.decisions.ignoredBody}</small>
          </span>
          <span className="ignored-summary-meta">
            {ignored.data?.items.length ?? 0}{" "}
            {copy.decisions.ignoredUnit}
            <ChevronDown size={15} aria-hidden="true" />
          </span>
        </summary>
        <div className="ignored-card-content">
          <label className="compact-search">
            <Search size={13} aria-hidden="true" />
            <span className="sr-only">{copy.decisions.searchIgnored}</span>
            <input
              type="search"
              value={ignoredSearch}
              placeholder={copy.decisions.searchIgnored}
              onChange={(event) => setIgnoredSearch(event.target.value)}
            />
          </label>
          <div className="ignored-list">
            {ignored.status === "error" ? (
              <p className="error-notice" role="alert">
                {copy.decisions.ignoredLoadError}
                <Button size="sm" onClick={ignored.reload}>
                  {copy.common.retry}
                </Button>
              </p>
            ) : null}
            {(ignored.data?.items ?? [])
              .filter((item) =>
                `${item.title} ${item.rationale}`
                  .toLocaleLowerCase()
                  .includes(ignoredSearch.trim().toLocaleLowerCase())
              )
              .map((item) => (
                <article key={item.id}>
                  <span>
                    <strong>{item.title}</strong>
                    <small>{item.rationale}</small>
                  </span>
                  <Button
                    tone="secondary"
                    size="sm"
                    onClick={() =>
                      api
                        .processIgnored(item.id)
                        .then(() => ignored.reload())
                        .catch((reason: unknown) =>
                          setError(
                            reason instanceof Error
                              ? reason.message
                              : String(reason)
                          )
                        )
                    }
                  >
                    {copy.decisions.reverseIgnore}
                  </Button>
                </article>
              ))}
          </div>
        </div>
      </details>
    </>
  );
}

export function Channels({
  onNavigate,
  onOpenChannel
}: {
  onNavigate: (view: View) => void;
  onOpenChannel?: (channelId: number) => void;
}) {
  const loadChannels = useCallback(
    (signal: AbortSignal) => api.channels(signal),
    []
  );
  const resource = useApiResource(loadChannels);
  const channels =
    resource.status === "ready"
      ? (resource.data?.items ?? []).map((channel) => [
          channel.title,
          channelModeLabel(channel.mode),
          copy.channels.monitoring
        ])
      : [];
  const [notice, setNotice] = useState("");

  return (
    <>
      <PageHeading
        title={copy.channels.title}
        description={copy.channels.description}
        actions={
          <>
            <Button
              tone="secondary"
              onClick={() => {
                setNotice(copy.channels.discovering);
                api
                  .discover()
                  .then(() => {
                    setNotice(copy.channels.discoverComplete);
                    resource.reload();
                  })
                  .catch((reason: unknown) =>
                    setNotice(
                      reason instanceof Error
                        ? reason.message
                        : String(reason)
                    )
                  );
              }}
            >
              <RotateCcw size={14} aria-hidden="true" />
              {copy.channels.discover}
            </Button>
            <Button onClick={() => onNavigate("add-channel")}>
              <Plus size={14} aria-hidden="true" />
              {copy.channels.add}
            </Button>
          </>
        }
      />
      {notice ? <p className="inline-notice">{notice}</p> : null}
      <Card className="channel-list">
        {resource.status === "loading" ? (
          <AsyncResourceState
            kind="loading"
            className="empty-state"
            icon={<RotateCcw size={20} aria-hidden="true" />}
            title={copy.channels.loadingTitle}
            detail={copy.channels.loadingBody}
          />
        ) : null}
        {resource.status === "error" ? (
          <AsyncResourceState
            kind="error"
            className="empty-state"
            icon={<CircleAlert size={20} aria-hidden="true" />}
            title={copy.channels.loadErrorTitle}
            detail={resource.error}
            onRetry={resource.reload}
            retryLabel={copy.common.retry}
          />
        ) : null}
        {resource.status === "ready" && channels.length === 0 ? (
          <div className="empty-state">
            <RadioIcon size={20} aria-hidden="true" />
            <strong>{copy.channels.emptyTitle}</strong>
            <span>{copy.channels.emptyBody}</span>
          </div>
        ) : null}
        {channels.map((channel, index) => (
          <article className="channel-row" key={channel[0]}>
            <span className="channel-mark">
              <RadioIcon size={17} aria-hidden="true" />
            </span>
            <span className="channel-copy">
              <button
                className="channel-title-button"
                type="button"
                onClick={() => {
                  if (resource.status === "ready") {
                    const source = resource.data?.items[index];
                    if (source) {
                      onOpenChannel?.(source.id);
                    }
                  }
                }}
              >
                {channel[0]}
              </button>
              <small>{channel[2]}</small>
            </span>
            <Badge tone={index === 2 ? "neutral" : "primary"}>{channel[1]}</Badge>
            <IconButton
              label={`${copy.common.open} ${channel[0]}`}
              onClick={() => {
                if (resource.status === "ready") {
                  const source = resource.data?.items[index];
                  if (source?.url) {
                    window.open(source.url, "_blank", "noopener");
                  }
                }
              }}
            >
              <ExternalLink size={15} aria-hidden="true" />
            </IconButton>
          </article>
        ))}
      </Card>
    </>
  );
}

export function AddChannel({
  onNavigate,
  onSaved
}: {
  onNavigate: (view: View) => void;
  onSaved?: (channelId: number) => void;
}) {
  const [url, setUrl] = useState("");
  const [resolution, setResolution] = useState<ApiSourceResolution | null>(
    null
  );
  const [resolving, setResolving] = useState(false);
  const [backfill, setBackfill] = useState<ApiBackfillItem[] | null>(null);
  const [backfillLoading, setBackfillLoading] = useState(false);
  const [mode, setMode] = useState("agent");
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const resolve = useCallback((value: string) => {
    if (!value) {
      return;
    }
    setResolving(true);
    setError("");
    setResolution(null);
    setBackfill(null);
    setBackfillLoading(false);
    setSelected([]);
    api
      .resolveSource(value)
      .then((result) => {
        setResolution(result);
        setBackfillLoading(true);
        return api
          .resolutionBackfill(result.resolution_id)
          .then((response) => {
            setBackfill(response.items);
            if (response.channel_avatar_url) {
              setResolution((current) =>
                current && !current.channel.avatar_url
                  ? {
                      ...current,
                      channel: {
                        ...current.channel,
                        avatar_url: response.channel_avatar_url
                      }
                    }
                  : current
              );
            }
          })
          .catch(() => setBackfill([]))
          .finally(() => setBackfillLoading(false));
      })
      .catch((reason: unknown) => {
        setError(
          reason instanceof Error ? reason.message : String(reason)
        );
      })
      .finally(() => setResolving(false));
  }, []);

  const requested = resolution?.requested_video ?? null;
  const queueCount = (requested ? 1 : 0) + selected.length;

  return (
    <div className="narrow-page">
      <PageHeading
        title={copy.channels.addTitle}
        description={copy.channels.addDescription}
      />
      <Card className="form-card">
        <Field label={copy.channels.urlLabel}>
          <div className="input-action">
            <input
              value={url}
              disabled={busy}
              placeholder={copy.channels.urlPlaceholder}
              onChange={(event) => {
                setUrl(event.target.value);
                setResolution(null);
                setBackfill(null);
                setSelected([]);
              }}
              onPaste={(event) => {
                const pasted = event.clipboardData
                  .getData("text")
                  .trim();
                if (pasted.startsWith("http")) {
                  event.preventDefault();
                  setUrl(pasted);
                  resolve(pasted);
                }
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  resolve(url);
                }
              }}
            />
            <Button
              tone="secondary"
              disabled={!url || resolving}
              onClick={() => resolve(url)}
            >
              {resolving
                ? copy.channels.resolving
                : copy.channels.resolve}
            </Button>
          </div>
        </Field>

        {resolving ? (
          <p className="resolving-note" role="status">
            <RotateCcw size={13} aria-hidden="true" />
            {copy.channels.resolvingSource}
          </p>
        ) : null}

        {requested ? (
          <div className="video-preview">
            <span className="video-preview-thumb">
              <img
                src={DEFAULT_ARTWORK_URL}
                alt=""
                onError={(event) => {
                  event.currentTarget.style.display = "none";
                }}
              />
              <i>{formatClockDuration(requested.duration_seconds)}</i>
            </span>
            <span className="video-preview-copy">
              <strong>{requested.title}</strong>
              <small>
                {copy.channels.publishedPrefix}
                {formatDateZh(requested.published_at)}
              </small>
              {requested.description ? (
                <p>{requested.description}</p>
              ) : null}
              <span className="video-preview-flags">
                <Badge tone="success">
                  <Check size={11} aria-hidden="true" />
                  {copy.channels.videoResolved}
                </Badge>
                <Badge tone="primary">
                  {copy.channels.queueAfterSave}
                </Badge>
              </span>
            </span>
          </div>
        ) : null}

        {resolution ? (
          <div className="channel-preview">
            <span className="channel-preview-avatar">
              <i>{channelInitials(resolution.channel.title)}</i>
            </span>
            <span className="channel-preview-copy">
              <strong>{resolution.channel.title}</strong>
              <small>{channelMetaLine(resolution.channel)}</small>
            </span>
            <Badge tone="success">
              <Check size={11} aria-hidden="true" />
              {copy.channels.channelResolved}
            </Badge>
          </div>
        ) : null}

        <fieldset className="mode-fieldset">
          <legend>{copy.channels.mode}</legend>
          <RadioGroup
            className="mode-grid"
            value={mode}
            onValueChange={(value) => setMode(value as string)}
          >
            {[
              ["agent", copy.channels.agent, copy.channels.modeAgentDescription],
              ["always", copy.channels.always, copy.channels.modeAlwaysDescription],
              ["paused", copy.channels.paused, copy.channels.modePausedDescription]
            ].map(([value, title, note]) => (
              <label className="mode-option" key={value}>
                <Radio.Root className="radio-item" value={value}>
                  <Radio.Indicator className="radio-indicator" />
                </Radio.Root>
                <span>
                  <strong>{title}</strong>
                  <small>{note}</small>
                </span>
              </label>
            ))}
          </RadioGroup>
        </fieldset>

        <section className="backfill">
          <header>
            <strong>{copy.channels.backfill}</strong>
            <small>{copy.channels.backfillDescription}</small>
          </header>
          {resolution && backfillLoading ? (
            <p className="backfill-loading" role="status">
              <RotateCcw size={13} aria-hidden="true" />
              {copy.channels.backfillLoading}
            </p>
          ) : null}
          {backfill && backfill.length > 0 ? (
            <>
              <div className="backfill-toolbar">
                <small>{copy.channels.backfillRecent}</small>
                <button
                  className="text-button"
                  type="button"
                  onClick={() =>
                    setSelected(
                      backfill
                        .filter((item) => !item.is_requested)
                        .map((item) => item.youtube_video_id)
                    )
                  }
                >
                  {copy.channels.backfillSelectAll}
                </button>
              </div>
              {backfill.map((video) => (
                <label
                  className="backfill-row backfill-video-row"
                  key={video.youtube_video_id}
                >
                  <Checkbox.Root
                    className="checkbox-root"
                    checked={
                      video.is_requested ||
                      selected.includes(video.youtube_video_id)
                    }
                    disabled={video.is_requested}
                    onCheckedChange={(checked) =>
                      setSelected((current) =>
                        checked
                          ? [...current, video.youtube_video_id]
                          : current.filter(
                              (value) =>
                                value !== video.youtube_video_id
                            )
                      )
                    }
                  >
                    <Checkbox.Indicator>
                      <Check size={12} aria-hidden="true" />
                    </Checkbox.Indicator>
                  </Checkbox.Root>
                  <span className="backfill-thumb">
                    <img
                      src={DEFAULT_ARTWORK_URL}
                      alt=""
                      onError={(event) => {
                        event.currentTarget.style.display = "none";
                      }}
                    />
                  </span>
                  <span className="backfill-copy">
                    <span>{video.title}</span>
                    <small>{videoMetaLine(video)}</small>
                  </span>
                  {video.is_requested ? (
                    <Badge tone="primary">
                      {copy.channels.supportedSourceVideo}
                    </Badge>
                  ) : (
                    <small>
                      {formatClockDuration(video.duration_seconds)}
                    </small>
                  )}
                </label>
              ))}
            </>
          ) : null}
        </section>

        <footer className="form-actions">
          <span className="form-note">{copy.channels.saveNote}</span>
          <Button tone="ghost" onClick={() => onNavigate("channels")}>
            {copy.common.cancel}
          </Button>
          <Button
            disabled={!resolution || busy}
            onClick={() => {
              if (!resolution) {
                return;
              }
              setBusy(true);
              setError("");
              api
                .createChannelFromResolution(
                  resolution.resolution_id,
                  modeValue(mode),
                  selected
                )
                .then((channel) => onSaved?.(channel.id))
                .catch((reason: unknown) => {
                  if (import.meta.env.DEV) {
                    onSaved?.(1);
                    return;
                  }
                  setError(
                    reason instanceof Error
                      ? reason.message
                      : String(reason)
                  );
                })
                .finally(() => setBusy(false));
            }}
          >
            {copy.channels.saveQueuePrefix}
            {queueCount}
            {copy.channels.saveQueueSuffix}
          </Button>
        </footer>
        {error ? <p className="error-notice">{error}</p> : null}
      </Card>
    </div>
  );
}

export function ChannelDetail({
  channelId,
  justSaved,
  onNavigate
}: {
  channelId: number | null;
  justSaved?: boolean;
  onNavigate: (view: View) => void;
}) {
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [refreshCount, setRefreshCount] = useState(0);
  const [addingId, setAddingId] = useState<string | null>(null);
  const [addedIds, setAddedIds] = useState<string[]>([]);
  const [selectedMode, setSelectedMode] =
    useState<ApiChannel["mode"]>("agent_filtering");
  const [modeSaving, setModeSaving] = useState(false);
  const [modeNotice, setModeNotice] = useState("");
  const [modeError, setModeError] = useState("");
  const loadChannel = useCallback(
    (signal: AbortSignal) =>
      channelId
        ? api.channelDetail(channelId, signal)
        : Promise.reject(new Error("Source Channel ID is required")),
    [channelId]
  );
  const channel = useApiResource<ApiChannelDetail | null>(loadChannel);
  const loadRecent = useCallback(
    (signal: AbortSignal) =>
      channelId
        ? api.channelRecentVideos(channelId, refreshCount > 0, signal)
        : Promise.reject(new Error("Source Channel ID is required")),
    [channelId, refreshCount]
  );
  const recent = useApiResource<{ items: ApiChannelRecentVideo[] }>(loadRecent);
  const loadSettings = useCallback(
    (signal: AbortSignal) => api.settings(signal),
    []
  );
  const settings = useApiResource<ApiSettings | null>(loadSettings);
  const detail = channel.data;
  const processingStartLabel =
    settings.status === "ready" && settings.data
      ? settings.data.automation.manual_processing
        ? copy.channelDetail.processingStartManual
        : copy.channelDetail.processingStartAutomatic
      : settings.status === "loading"
        ? copy.channelDetail.processingStartLoading
        : copy.channelDetail.processingStartUnavailable;

  useEffect(() => {
    if (detail?.mode) {
      setSelectedMode(detail.mode);
    }
  }, [detail?.mode]);

  const saveMode = (nextMode: ApiChannel["mode"]) => {
    if (!channelId || nextMode === selectedMode || modeSaving) {
      return;
    }
    const previousMode = selectedMode;
    setSelectedMode(nextMode);
    setModeSaving(true);
    setModeNotice(copy.channelDetail.modeSaving);
    setModeError("");
    api
      .updateChannelMode(channelId, nextMode)
      .then((updated) => {
        setSelectedMode(updated.mode);
        setModeNotice(copy.channelDetail.modeSaved);
        channel.reload();
      })
      .catch((reason: unknown) => {
        setSelectedMode(previousMode);
        setModeNotice("");
        setModeError(
          reason instanceof Error ? reason.message : String(reason)
        );
      })
      .finally(() => setModeSaving(false));
  };

  if (channel.status === "loading") {
    return (
      <AsyncResourceState
        kind="loading"
        surface="card"
        className="empty-state"
        icon={<RotateCcw size={20} aria-hidden="true" />}
        title={copy.channelDetail.loadingChannelTitle}
        detail={copy.channelDetail.loadingChannelBody}
      />
    );
  }

  if (channel.status === "error" || !detail) {
    return (
      <AsyncResourceState
        kind="error"
        surface="card"
        className="empty-state"
        icon={<CircleAlert size={20} aria-hidden="true" />}
        title={copy.channelDetail.loadErrorTitle}
        detail={channel.error}
        onRetry={channel.reload}
        retryLabel={copy.common.retry}
      />
    );
  }

  return (
    <>
      {justSaved ? (
        <p className="success-toast" role="status">
          <CheckCircle2 size={14} aria-hidden="true" />
          {copy.channelDetail.savedToast}
        </p>
      ) : null}

      <Card className="channel-hero">
        <span className="channel-hero-avatar">
          <i>{channelInitials(detail?.title ?? "")}</i>
        </span>
        <span className="channel-hero-copy">
          <h1>{detail?.title ?? ""}</h1>
          <p>{detail?.url ?? ""}</p>
        </span>
      </Card>

      <Card className="channel-policy-card">
        <header>
          <div>
            <h2>{copy.channelDetail.modeTitle}</h2>
            <p>{copy.channelDetail.modeDescription}</p>
          </div>
          <Badge tone="primary">
            {channelModeLabel(selectedMode)}
          </Badge>
        </header>
        <RadioGroup
          aria-label={copy.channelDetail.modeTitle}
          className="mode-grid channel-mode-grid"
          value={selectedMode}
          onValueChange={(value) =>
            saveMode(value as ApiChannel["mode"])
          }
        >
          {[
            [
              "agent_filtering",
              copy.channels.agent,
              copy.channels.modeAgentDescription
            ],
            [
              "process_every_video",
              copy.channels.always,
              copy.channels.modeAlwaysDescription
            ],
            [
              "paused",
              copy.channels.paused,
              copy.channels.modePausedDescription
            ]
          ].map(([value, title, note]) => (
            <label className="mode-option" key={value}>
              <Radio.Root
                className="radio-item"
                disabled={modeSaving}
                value={value}
              >
                <Radio.Indicator className="radio-indicator" />
              </Radio.Root>
              <span>
                <strong>{title}</strong>
                <small>{note}</small>
              </span>
            </label>
          ))}
        </RadioGroup>
        <p className="channel-mode-future-note">
          {copy.channelDetail.modeFutureOnly}
        </p>
        <footer className="channel-processing-policy">
          <span>
            <small>{copy.channelDetail.processingStartTitle}</small>
            <strong>{processingStartLabel}</strong>
            <em>{copy.channelDetail.processingStartDescription}</em>
          </span>
          <Button
            tone="ghost"
            size="sm"
            onClick={() => onNavigate("settings")}
          >
            {copy.channelDetail.openAutomationSettings}
          </Button>
        </footer>
        {modeNotice ? (
          <p className="channel-mode-notice" role="status">
            {modeNotice}
          </p>
        ) : null}
        {modeError ? (
          <p className="error-notice" role="alert">
            {modeError}
          </p>
        ) : null}
      </Card>

      <Card className="recent-videos-card">
        <header className="recent-videos-header">
          <div>
            <h2>{copy.channelDetail.recentTitle}</h2>
            <small>{copy.channelDetail.recentHint}</small>
          </div>
          <Button
            tone="secondary"
            size="sm"
            onClick={() => setRefreshCount((count) => count + 1)}
          >
            <RotateCcw size={13} aria-hidden="true" />
            {copy.channelDetail.refresh}
          </Button>
        </header>
        {recent.status === "loading" ? (
          <div className="recent-videos-loading" role="status">
            <span className="skeleton-row" />
            <span className="skeleton-row" />
            <span className="skeleton-row" />
            <p>
              <RotateCcw size={13} aria-hidden="true" />
              {copy.channelDetail.loading}
            </p>
          </div>
        ) : recent.status === "error" ? (
          <AsyncResourceState
            kind="error"
            className="empty-state"
            icon={<CircleAlert size={18} aria-hidden="true" />}
            title={copy.channelDetail.recentLoadError}
            detail={recent.error}
            onRetry={recent.reload}
            retryLabel={copy.common.retry}
          />
        ) : (
          <div className="recent-videos-list">
            {(recent.data?.items ?? []).map((video) => (
              <article
                className="recent-video-row"
                key={video.youtube_video_id}
              >
                <span className="recent-video-thumb">
                  <img
                    src={DEFAULT_ARTWORK_URL}
                    alt=""
                    onError={(event) => {
                      event.currentTarget.style.display = "none";
                    }}
                  />
                  <i>{formatClockDuration(video.duration_seconds)}</i>
                </span>
                <span className="recent-video-copy">
                  <strong>{video.title}</strong>
                  <small>{videoMetaLine(video)}</small>
                </span>
                <RecentVideoActions
                  video={video}
                  adding={addingId === video.youtube_video_id}
                  added={addedIds.includes(video.youtube_video_id)}
                  onView={() => onNavigate("episodes")}
                  onAdd={() => {
                    if (!channelId) {
                      setAddedIds((current) => [
                        ...current,
                        video.youtube_video_id
                      ]);
                      setNotice(copy.channelDetail.added);
                      return;
                    }
                    setError("");
                    setAddingId(video.youtube_video_id);
                    api
                      .importChannelVideo(
                        channelId,
                        video.youtube_video_id
                      )
                      .then(() => {
                        setAddedIds((current) => [
                          ...current,
                          video.youtube_video_id
                        ]);
                        setNotice(copy.channelDetail.added);
                      })
                      .catch((reason: unknown) =>
                        setError(
                          reason instanceof Error
                            ? reason.message
                            : String(reason)
                        )
                      )
                      .finally(() => setAddingId(null));
                  }}
                />
              </article>
            ))}
          </div>
        )}
        <p className="recent-videos-note">
          {copy.channelDetail.listNote}
        </p>
        {notice ? <p className="success-notice">{notice}</p> : null}
        {error ? <p className="error-notice">{error}</p> : null}
      </Card>
    </>
  );
}

function RecentVideoActions({
  video,
  adding,
  added,
  onView,
  onAdd
}: {
  video: ApiChannelRecentVideo;
  adding: boolean;
  added: boolean;
  onView: () => void;
  onAdd: () => void;
}) {
  if (video.episode_id !== null) {
    return (
      <span className="recent-video-state">
        <Badge tone="success">{copy.channelDetail.stateDone}</Badge>
      </span>
    );
  }
  if (video.run_status !== null || added) {
    const label =
      video.run_status === null ||
      video.run_status === "pending_start"
        ? copy.channelDetail.stateWaiting
        : video.run_status === "processing"
          ? copy.channelDetail.stateProcessing
          : copy.channelDetail.stateQueued;
    return (
      <span className="recent-video-state">
        <Badge tone="warning">{label}</Badge>
        <Button tone="secondary" size="sm" onClick={onView}>
          {copy.channelDetail.viewQueue}
        </Button>
      </span>
    );
  }
  if (video.candidate_status === "awaiting_decision") {
    return (
      <span className="recent-video-state">
        <Badge tone="warning">{copy.channelDetail.stateAwaiting}</Badge>
      </span>
    );
  }
  if (video.candidate_status === "ignored") {
    return (
      <span className="recent-video-state">
        <Badge>{copy.channelDetail.stateIgnored}</Badge>
      </span>
    );
  }
  return (
    <span className="recent-video-state">
      <Button size="sm" disabled={adding} onClick={onAdd}>
        {adding ? (
          <RotateCcw
            className="spinning-icon"
            size={13}
            aria-hidden="true"
          />
        ) : (
          <Plus size={13} aria-hidden="true" />
        )}
        {adding
          ? copy.channelDetail.addingToQueue
          : copy.channelDetail.addToQueue}
      </Button>
    </span>
  );
}

function parseTopics(value: string): string[] {
  try {
    const parsed = JSON.parse(value) as unknown;
    return Array.isArray(parsed)
      ? parsed.map((item) => String(item))
      : [];
  } catch {
    return [];
  }
}

function formatDecisionRationale(value: string): string {
  if (
    value.trim().toLocaleLowerCase() ===
    "available triage evidence is insufficient."
  ) {
    return copy.decisions.insufficientEvidence;
  }
  return value || copy.decisions.rationale;
}

function summarizeCandidateDescription(value: string): string {
  const lines = value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const contentLines: string[] = [];
  for (const line of lines) {
    if (/^thanks to our partners/i.test(line)) {
      break;
    }
    if (
      /^https?:\/\//i.test(line) ||
      /^follow (on|the)/i.test(line) ||
      /^#/i.test(line)
    ) {
      continue;
    }
    contentLines.push(line);
  }
  const summary = contentLines.join(" ");
  if (!summary) {
    return copy.decisions.summaryFallback;
  }
  return summary.length > 360
    ? `${summary.slice(0, 357).trim()}…`
    : summary;
}

function formatDuration(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours > 0
    ? copy.common.durationHoursMinutes(hours, minutes)
    : copy.common.durationMinutes(minutes);
}

function formatClockDuration(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const remaining = safe % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(
        remaining
      ).padStart(2, "0")}`
    : `${minutes}:${String(remaining).padStart(2, "0")}`;
}

function formatDateZh(value: string | null): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return copy.common.fullDate(
    date.getFullYear(),
    date.getMonth() + 1,
    date.getDate()
  );
}

function formatViews(count: number | null): string {
  if (count === null) {
    return "";
  }
  if (count >= 10_000) {
    const wan = count / 10_000;
    const rendered =
      wan >= 100 ? String(Math.round(wan)) : wan.toFixed(1);
    return `${copy.common.tenThousands(rendered)}${copy.channels.viewCountSuffix}`;
  }
  return `${count}${copy.channels.viewCountSuffix}`;
}

function channelInitials(title: string): string {
  const words = title
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase() ?? "");
  return words.join("") || title.slice(0, 2);
}

function channelMetaLine(channel: ApiSourceResolution["channel"]): string {
  const parts: string[] = [];
  if (channel.video_count !== null) {
    parts.push(`${channel.video_count}${copy.channels.videoCountSuffix}`);
  }
  if (channel.subscriber_count !== null) {
    const wan = channel.subscriber_count / 10_000;
    parts.push(
      channel.subscriber_count >= 10_000
        ? `${copy.common.tenThousands(wan >= 100 ? Math.round(wan) : wan.toFixed(1))}${copy.channels.subscriberSuffix}`
        : `${channel.subscriber_count}${copy.channels.subscriberSuffix}`
    );
  }
  parts.unshift(channel.url.replace(/^https?:\/\/(www\.)?/, ""));
  return parts.join(" · ");
}

function videoMetaLine(
  video: Pick<ApiBackfillItem, "published_at" | "view_count">
): string {
  return [formatDateZh(video.published_at), formatViews(video.view_count)]
    .filter(Boolean)
    .join(" · ");
}

function channelModeLabel(mode: ApiChannel["mode"]): string {
  if (mode === "agent_filtering") {
    return copy.channels.agent;
  }
  if (mode === "process_every_video") {
    return copy.channels.always;
  }
  return copy.channels.paused;
}

function modeValue(mode: string): ApiChannel["mode"] {
  if (mode === "always") {
    return "process_every_video";
  }
  if (mode === "paused") {
    return "paused";
  }
  return "agent_filtering";
}
