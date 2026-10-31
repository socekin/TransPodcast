import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { RingArt } from "../components/Art";
import {
  type MenuItem,
  MixedHeading,
  MoreMenu,
  PageHeader,
  SpeedMenu
} from "../components/Controls";
import {
  CheckIcon,
  ForwardIcon,
  PauseIcon,
  PlayIcon,
  RewindIcon
} from "../components/Icons";
import { StepList } from "../components/StepList";
import { copy } from "../i18n/zh-CN";
import {
  api,
  type ApiEpisode,
  type ApiListeningAuditFinding,
  type ApiRun,
  type ApiTranscriptLine,
  asListeningAudit,
  episodeAudioUrl
} from "../lib/api";
import {
  errorMessage,
  formatClock,
  formatDay,
  formatElapsed,
  formatLength,
  speakerName
} from "../lib/format";
import { PublicationDecisionRequest } from "../lib/generated";
import { useLibrary } from "../lib/library";
import { sameTrack, type Track, usePlayer } from "../lib/player";
import { agentName, runElapsedMs, voiceName } from "../lib/runSteps";
import { useStableResource } from "../lib/useStableResource";
import { cn } from "../lib/utils";
import { findingDetail, findingKind, findingOrdinal } from "./findings";

type Kind = "ready" | "published" | "unpublished" | "removed";
type TabId = "preview" | "transcript" | "summary" | "quality" | "log";

function episodeKind(state: string): Kind {
  if (state === "published") {
    return "published";
  }
  if (state === "unpublished") {
    return "unpublished";
  }
  if (state === "deleted") {
    return "removed";
  }
  return "ready";
}

function isGenericChapter(title: string): boolean {
  return /^(part|chapter|section)\s*\d+$/i.test(title.trim()) ||
    /^第\s*\d+\s*(部分|章|节)$/.test(title.trim());
}

export function EpisodePage({ id }: { id: number }) {
  const load = useCallback(
    (signal: AbortSignal) => api.episode(id, signal),
    [id]
  );
  const resource = useStableResource(load, { resourceKey: id });
  const episode = resource.data;

  if (!episode) {
    return (
      <div className="page detail-page">
        <PageHeader title={copy.episode.detailTitle} />
        <p className="muted-paragraph page-status" role={resource.error ? "alert" : "status"}>
          {resource.error
            ? `${copy.episode.loadError}：${resource.error}`
            : copy.episode.loading}
        </p>
      </div>
    );
  }
  return <EpisodeView episode={episode} reload={resource.refresh} />;
}

function EpisodeView({
  episode,
  reload
}: {
  episode: ApiEpisode;
  reload: () => Promise<unknown>;
}) {
  const player = usePlayer();
  const library = useLibrary();
  const kind = episodeKind(episode.state);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const listener = kind === "published" ? episode.published_metadata : null;
  const title = listener?.title ?? episode.title;
  const duration = listener?.duration_seconds ?? episode.duration_seconds;
  const track: Track = useMemo(
    () => ({
      episodeId: episode.id,
      sourceUrl: episodeAudioUrl(episode.id, kind === "published"),
      title,
      subtitle: episode.source_show,
      artworkUrl: episode.artwork_url,
      duration
    }),
    [duration, episode.artwork_url, episode.id, episode.source_show, kind, title]
  );
  const loaded = sameTrack(player.track, track);
  const playing = loaded && player.playing;
  const totalSeconds = loaded && player.duration > 0 ? player.duration : duration;
  const position = loaded ? player.currentTime : 0;
  const progress = totalSeconds > 0 ? position / totalSeconds : 0;

  const togglePlay = () => (loaded ? player.toggle() : player.play(track));
  const playFrom = (seconds: number) => player.play(track, seconds);

  const act = (work: () => Promise<unknown>) => {
    setBusy(true);
    setNotice("");
    work()
      .then(() => Promise.all([reload(), library.refresh()]))
      .catch((reason: unknown) => setNotice(errorMessage(reason)))
      .finally(() => setBusy(false));
  };
  const decide = (decision: PublicationDecisionRequest.decision) =>
    act(() => api.decidePublication(episode.id, decision, episode.state));

  const menu: MenuItem[] = [
    {
      label: copy.actions.openSource,
      onSelect: () => window.open(episode.source_url, "_blank", "noopener")
    }
  ];
  if (kind === "published") {
    menu.push({
      label: copy.actions.takeDown,
      tone: "danger",
      onSelect: () => {
        if (window.confirm(copy.actions.takeDownConfirm)) {
          act(() => api.takeDown(episode.id));
        }
      }
    });
  }

  const subtitle =
    kind === "published"
      ? `${episode.source_show} · ${copy.episode.publishedOn(formatDay(episode.published_at))}`
      : kind === "ready"
        ? `${episode.source_show} · ${copy.episode.readySubtitle}`
        : kind === "unpublished"
          ? `${episode.source_show} · ${copy.episode.unpublishedSubtitle}`
          : `${episode.source_show} · ${copy.episode.removedSubtitle}`;

  const pageTitle =
    kind === "published"
      ? copy.episode.listening
      : kind === "ready"
        ? copy.episode.readyTitle
        : copy.episode.detailTitle;

  return (
    <div className="page detail-page">
      <PageHeader title={pageTitle} right={<MoreMenu items={menu} />} />
      <div className="detail-grid">
        <section className="player-column" aria-label={copy.actions.play}>
          <div className="player-meta">
            <p className="player-clock">
              <span>{formatClock(position)}</span> | {formatClock(totalSeconds)}
            </p>
            <SpeedMenu
              rate={player.rate}
              onChange={player.setRate}
              note={copy.speed.global}
            />
          </div>
          <RingArt
            src={episode.artwork_url}
            progress={progress}
            label={title}
            valueText={`${formatClock(position)} / ${formatClock(totalSeconds)}`}
            onSeek={(fraction) => {
              const seconds = fraction * totalSeconds;
              if (loaded) {
                player.seek(seconds);
              } else {
                playFrom(seconds);
              }
            }}
          />
          <h2 className="player-title">{title}</h2>
          <p className="player-subtitle">{subtitle}</p>
          {kind === "published" ? (
            <div className="transport">
              <button
                type="button"
                className="circle-button is-transport"
                aria-label={copy.actions.rewind}
                onClick={() => (loaded ? player.skip(-15) : playFrom(0))}
              >
                <RewindIcon size={20} />
              </button>
              <button
                type="button"
                className="play-button is-large"
                aria-label={playing ? copy.actions.pause : copy.actions.play}
                onClick={togglePlay}
              >
                {playing ? <PauseIcon size={26} /> : <PlayIcon size={26} />}
              </button>
              <button
                type="button"
                className="circle-button is-transport"
                aria-label={copy.actions.forward}
                onClick={() => (loaded ? player.skip(30) : playFrom(30))}
              >
                <ForwardIcon size={20} />
              </button>
            </div>
          ) : (
            <div className="transport is-decision">
              <button
                type="button"
                className="play-button is-outline"
                aria-label={playing ? copy.actions.pause : copy.actions.audition}
                onClick={togglePlay}
              >
                {playing ? <PauseIcon size={22} /> : <PlayIcon size={22} />}
              </button>
              {kind === "ready" || kind === "unpublished" ? (
                <button
                  type="button"
                  className="pill-button is-primary is-tall"
                  disabled={busy}
                  onClick={() => decide(PublicationDecisionRequest.decision.PUBLISH)}
                >
                  {busy ? copy.actions.working : copy.actions.publishToFeed}
                </button>
              ) : null}
              {kind === "ready" ? (
                <button
                  type="button"
                  className="pill-button is-outline is-tall"
                  disabled={busy}
                  onClick={() =>
                    decide(PublicationDecisionRequest.decision.DO_NOT_PUBLISH)
                  }
                >
                  {copy.actions.decline}
                </button>
              ) : null}
              {kind === "removed" && episode.recoverable ? (
                <button
                  type="button"
                  className="pill-button is-primary is-tall"
                  disabled={busy}
                  onClick={() => act(() => api.recover(episode.id))}
                >
                  {copy.actions.recover}
                </button>
              ) : null}
            </div>
          )}
          {kind === "removed" ? (
            <p className="muted-note">
              {episode.recoverable
                ? copy.episode.removedNotice
                : copy.episode.recoveryExpired}
            </p>
          ) : null}
          {notice ? (
            <p className="panel-notice" role="alert">
              {notice}
            </p>
          ) : null}
        </section>

        <EpisodeTabs
          episode={episode}
          kind={kind}
          track={track}
          onPlayFrom={playFrom}
        />
      </div>
    </div>
  );
}

function EpisodeTabs({
  episode,
  kind,
  track,
  onPlayFrom
}: {
  episode: ApiEpisode;
  kind: Kind;
  track: Track;
  onPlayFrom: (seconds: number) => void;
}) {
  const tabs: TabId[] =
    kind === "ready"
      ? ["preview", "transcript", "quality", "log"]
      : ["transcript", "summary", "quality", "log"];
  const [tab, setTab] = useState<TabId>(tabs[0]);
  const active = tabs.includes(tab) ? tab : tabs[0];
  return (
    <section className="panel tab-panel" aria-label={copy.episode.tabsLabel}>
      <div className="tabs" role="tablist" aria-label={copy.episode.tabsLabel}>
        {tabs.map((id) => (
          <button
            key={id}
            type="button"
            role="tab"
            id={`tab-${id}`}
            aria-selected={active === id}
            aria-controls={`tabpanel-${id}`}
            className={cn("tab", active === id && "is-selected")}
            onClick={() => setTab(id)}
          >
            {copy.episode.tabs[id]}
          </button>
        ))}
      </div>
      <div
        className="tab-body"
        role="tabpanel"
        id={`tabpanel-${active}`}
        aria-labelledby={`tab-${active}`}
      >
        {active === "preview" ? <PreviewTab episode={episode} /> : null}
        {active === "transcript" ? (
          <TranscriptTab
            runId={episode.processing_run_id}
            track={track}
            onPlayFrom={onPlayFrom}
          />
        ) : null}
        {active === "summary" ? (
          <SummaryTab episode={episode} onPlayFrom={onPlayFrom} />
        ) : null}
        {active === "quality" ? (
          <QualityTab episode={episode} onPlayFrom={onPlayFrom} />
        ) : null}
        {active === "log" ? (
          <LogTab
            runId={episode.processing_run_id}
            sourceSeconds={episode.duration_seconds}
          />
        ) : null}
      </div>
    </section>
  );
}

function auditOf(episode: ApiEpisode) {
  return asListeningAudit(episode.quality_gates.listening_audit);
}

function PreviewTab({ episode }: { episode: ApiEpisode }) {
  const audit = auditOf(episode);
  const description = (episode.publication_description || episode.overview)
    .split(/\n\s*\n/)
    .filter(Boolean);
  const qualityNote = !audit
    ? copy.episode.qualityNoHard
    : audit.owner_acknowledged && audit.hard_count > 0
      ? copy.episode.qualityAcknowledged(audit.hard_count)
      : audit.soft_count > 0
        ? copy.episode.qualitySoft(audit.soft_count)
        : copy.episode.qualityNoHard;
  return (
    <div className="preview-tab">
      <p className="muted-note">{copy.episode.previewNote}</p>
      <article className="feed-preview">
        <p className="feed-preview-meta">
          {copy.episode.previewMeta(formatLength(episode.duration_seconds))}
        </p>
        <p className="feed-preview-title">{episode.title}</p>
        {description.map((paragraph) => (
          <p key={paragraph} className="feed-preview-body">
            {paragraph}
          </p>
        ))}
        <p className="muted-note">
          {copy.episode.sourceLine(episode.source_show, episode.source_title)}
        </p>
      </article>
      <ul className="check-list">
        <li>
          <span className="check-badge" aria-hidden="true">
            <CheckIcon size={15} />
          </span>
          <span className="check-label">{copy.episode.qualityPassed}</span>
          <span className="muted-note">{qualityNote}</span>
        </li>
        <li>
          <span className="check-badge" aria-hidden="true">
            <CheckIcon size={15} />
          </span>
          <span className="check-label">{copy.episode.publishImmediately}</span>
          <span className="muted-note">{copy.episode.publishReversible}</span>
        </li>
      </ul>
    </div>
  );
}

function TranscriptTab({
  runId,
  track,
  onPlayFrom
}: {
  runId: number;
  track: Track;
  onPlayFrom: (seconds: number) => void;
}) {
  const player = usePlayer();
  const [lines, setLines] = useState<ApiTranscriptLine[] | null>(null);
  const [error, setError] = useState("");
  const scroller = useRef<HTMLDivElement | null>(null);
  const lastManualScroll = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    setLines(null);
    setError("");
    api
      .transcript(runId, controller.signal)
      .then((value) =>
        setLines(
          value.lines.filter((line) => line.output_start_seconds !== null)
        )
      )
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(errorMessage(reason));
        }
      });
    return () => controller.abort();
  }, [runId]);

  const loaded = sameTrack(player.track, track);
  const time = loaded ? player.currentTime : -1;
  const current = useMemo(() => {
    if (!lines || time < 0) {
      return -1;
    }
    let index = -1;
    for (let i = 0; i < lines.length; i += 1) {
      if ((lines[i].output_start_seconds ?? 0) <= time + 0.05) {
        index = i;
      } else {
        break;
      }
    }
    return index;
  }, [lines, time]);

  useEffect(() => {
    if (current < 0 || Date.now() - lastManualScroll.current < 4000) {
      return;
    }
    const container = scroller.current;
    const node = container?.querySelector<HTMLElement>(`[data-line="${current}"]`);
    if (container && node) {
      // Keep the current line about a third of the way down.
      const offset =
        node.getBoundingClientRect().top -
        container.getBoundingClientRect().top +
        container.scrollTop;
      const target = offset - container.clientHeight * 0.3;
      container.scrollTo?.({ top: Math.max(0, target), behavior: "smooth" });
    }
  }, [current]);

  if (error) {
    return <p className="muted-paragraph" role="alert">{`${copy.episode.transcriptError}：${error}`}</p>;
  }
  if (!lines) {
    return <p className="muted-paragraph">{copy.episode.transcriptLoading}</p>;
  }
  if (!lines.length) {
    return <p className="muted-paragraph">{copy.episode.transcriptEmpty}</p>;
  }
  return (
    <div
      className="transcript"
      ref={scroller}
      onWheel={() => {
        lastManualScroll.current = Date.now();
      }}
      onTouchMove={() => {
        lastManualScroll.current = Date.now();
      }}
    >
      {lines.map((line, index) => (
        <button
          key={line.ordinal}
          type="button"
          data-line={index}
          className={cn("transcript-line", index === current && "is-current")}
          aria-current={index === current ? "true" : undefined}
          onClick={() => {
            lastManualScroll.current = 0;
            onPlayFrom(line.output_start_seconds ?? 0);
          }}
        >
          <span className="transcript-meta">
            {formatClock(line.output_start_seconds ?? 0)} ·{" "}
            {speakerName(line.speaker_label)}
          </span>
          <span className="transcript-text">{line.target_text}</span>
          {index === current && line.source_text ? (
            <span className="transcript-source">{line.source_text}</span>
          ) : null}
        </button>
      ))}
    </div>
  );
}

function SummaryTab({
  episode,
  onPlayFrom
}: {
  episode: ApiEpisode;
  onPlayFrom: (seconds: number) => void;
}) {
  const listener =
    episode.state === "published" ? episode.published_metadata : null;
  const text = listener?.description ?? episode.publication_description ?? "";
  const paragraphs = (text || episode.overview).split(/\n\s*\n/).filter(Boolean);
  const chapters = (listener?.chapters ?? episode.chapters).filter(
    (chapter) => !isGenericChapter(chapter.title)
  );
  return (
    <div className="summary-tab">
      {paragraphs.map((paragraph) => (
        <p key={paragraph} className="summary-paragraph">
          {paragraph}
        </p>
      ))}
      {chapters.length ? (
        <div className="chapter-list">
          <p className="muted-note">{copy.episode.chapters}</p>
          {chapters.map((chapter) => (
            <button
              key={`${chapter.start_seconds}-${chapter.title}`}
              type="button"
              className="chapter-row"
              onClick={() => onPlayFrom(chapter.start_seconds)}
            >
              <span className="chapter-time">{formatClock(chapter.start_seconds)}</span>
              <span>{chapter.title}</span>
            </button>
          ))}
        </div>
      ) : null}
      <p className="muted-note">
        {copy.episode.sourceLine(episode.source_show, episode.source_title)}
      </p>
      <a
        className="pill-button is-outline is-small"
        href={episode.source_url}
        target="_blank"
        rel="noopener noreferrer"
      >
        {copy.actions.openSource}
      </a>
    </div>
  );
}

function QualityTab({
  episode,
  onPlayFrom
}: {
  episode: ApiEpisode;
  onPlayFrom: (seconds: number) => void;
}) {
  const audit = auditOf(episode);
  const hardAcknowledged = Boolean(audit?.owner_acknowledged && audit.hard_count > 0);
  const findings: ApiListeningAuditFinding[] = audit?.findings ?? [];
  const retried = audit?.retry?.resolved_unit_ids?.length ?? 0;
  return (
    <div className="quality-tab">
      <MixedHeading
        bold={copy.quality.titleBold}
        light={
          hardAcknowledged || (audit?.soft_count ?? 0) > 0
            ? copy.quality.attention
            : copy.quality.passed
        }
        as="p"
        className="tab-heading"
      />
      <p className="muted-paragraph">
        {!audit
          ? copy.quality.unavailable
          : hardAcknowledged
            ? copy.quality.acknowledged(audit.hard_count, audit.soft_count)
            : copy.quality.summary(audit.soft_count, retried)}
      </p>
      {audit && !findings.length ? (
        <p className="muted-paragraph">{copy.quality.clean}</p>
      ) : null}
      <ul className="finding-list">
        {findings.map((finding, index) => {
          const ordinal = findingOrdinal(finding);
          return (
            <li key={`${finding.unit_id}-${finding.kind}-${index}`}>
              <span className="finding-text">
                <span className="finding-kind">
                  {findingKind(finding)}
                  {finding.severity === "hard" ? (
                    <span className="hard-tag">{copy.quality.hard}</span>
                  ) : null}
                </span>
                <span className="muted-note">
                  {ordinal !== null
                    ? copy.quality.line(ordinal)
                    : copy.quality.episodeWide}{" "}
                  · {findingDetail(finding)}
                </span>
              </span>
              {finding.output_start_seconds !== null ? (
                <button
                  type="button"
                  className="circle-button"
                  aria-label={`${copy.quality.listenLine}${
                    ordinal !== null ? ` ${copy.quality.line(ordinal)}` : ""
                  }`}
                  onClick={() => onPlayFrom(finding.output_start_seconds ?? 0)}
                >
                  <PlayIcon size={13} />
                </button>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function LogTab({
  runId,
  sourceSeconds
}: {
  runId: number;
  sourceSeconds: number;
}) {
  const [run, setRun] = useState<ApiRun | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    api
      .run(runId, controller.signal)
      .then(setRun)
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(errorMessage(reason));
        }
      });
    return () => controller.abort();
  }, [runId]);
  if (error) {
    return <p className="muted-paragraph" role="alert">{error}</p>;
  }
  if (!run) {
    return <p className="muted-paragraph">{copy.log.loading}</p>;
  }
  const elapsed = runElapsedMs(run, Date.now());
  const voice = voiceName(
    run.pipeline_steps.find((step) => step.id === "voice_render")
      ?.technical_evidence?.model
  );
  const facts = [
    copy.log.cost(run.estimated_cost_usd.toFixed(2)),
    agentName(run.translation.provider)
      ? copy.log.agent(agentName(run.translation.provider))
      : "",
    voice ? copy.log.voice(voice) : ""
  ].filter(Boolean);
  // "2 小时 13 分": the leading figure and its unit carry the weight.
  const parts = elapsed !== null ? formatElapsed(elapsed).split(" ") : [];
  return (
    <div className="log-tab">
      {parts.length ? (
        <p className="tab-heading mixed-heading">
          <b>{parts.slice(0, 2).join(" ")}</b> {parts.slice(2).join(" ")}
        </p>
      ) : null}
      <p className="muted-paragraph">{facts.join(" · ")}</p>
      <StepList run={run} sourceSeconds={sourceSeconds} />
    </div>
  );
}
