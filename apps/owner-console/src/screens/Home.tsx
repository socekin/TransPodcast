import {
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";

import { Artwork, Backdrop } from "../components/Art";
import {
  MixedHeading,
  RouteLink,
  SpeedCycle
} from "../components/Controls";
import {
  AlertIcon,
  ChevronRightIcon,
  CloseIcon,
  FeedIcon,
  PauseIcon,
  PlayIcon,
  SearchIcon,
  SlidersIcon
} from "../components/Icons";
import { copy } from "../i18n/zh-CN";
import {
  api,
  type ApiLibraryItem,
  type ApiRun,
  episodeAudioUrl,
  type Readiness
} from "../lib/api";
import {
  errorMessage,
  formatClock,
  formatDay,
  formatElapsed,
  formatLength
} from "../lib/format";
import { EpisodeLibraryState, PublicationDecisionRequest } from "../lib/generated";
import { activeItems, routeForItem, useLibrary } from "../lib/library";
import { sameTrack, type Track, usePlayer } from "../lib/player";
import {
  currentStep,
  finishedSteps,
  runElapsedMs,
  stepAudit,
  stepLabel
} from "../lib/runSteps";
import { cn } from "../lib/utils";
import { AddSheet } from "./AddSheet";

type Tab = "ready" | "published" | "removed";

export function trackForItem(item: ApiLibraryItem): Track | null {
  if (item.episode_id === null) {
    return null;
  }
  const published = item.library_state === EpisodeLibraryState.PUBLISHED;
  return {
    episodeId: item.episode_id,
    sourceUrl: episodeAudioUrl(item.episode_id, published),
    title: item.title,
    subtitle: item.source_show,
    artworkUrl: item.artwork_url,
    duration: item.duration_seconds
  };
}

export function useReadiness() {
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [checking, setChecking] = useState(false);
  const check = useCallback((signal?: AbortSignal) => {
    setChecking(true);
    return api
      .readiness(signal)
      .then(setReadiness)
      .catch(() => undefined)
      .finally(() => setChecking(false));
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void check(controller.signal);
    const timer = window.setInterval(() => void check(), 60000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [check]);
  return { readiness, checking, recheck: () => void check() };
}

/** First required service that is not ready, if any. */
export function blockingService(readiness: Readiness | null): string | null {
  if (!readiness || readiness.ready) {
    return null;
  }
  const entry = Object.entries(readiness.details).find(
    ([, detail]) => !detail.optional && !detail.configured
  );
  return entry?.[0] ?? null;
}

/** Live run details for the few active items, refreshed while they run. */
function useActiveRuns(ids: number[]): Record<number, ApiRun> {
  const [runs, setRuns] = useState<Record<number, ApiRun>>({});
  const key = ids.join(",");
  useEffect(() => {
    if (!key) {
      setRuns({});
      return;
    }
    const runIds = key.split(",").map(Number);
    let cancelled = false;
    const load = () => {
      if (document.visibilityState !== "visible") {
        return;
      }
      void Promise.allSettled(runIds.map((id) => api.run(id))).then(
        (results) => {
          if (cancelled) {
            return;
          }
          const next: Record<number, ApiRun> = {};
          results.forEach((result, index) => {
            if (result.status === "fulfilled") {
              next[runIds[index]] = result.value;
            }
          });
          setRuns(next);
        }
      );
    };
    load();
    const timer = window.setInterval(load, 10000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [key]);
  return runs;
}

export function Home() {
  const library = useLibrary();
  const { readiness } = useReadiness();
  const [sheetUrl, setSheetUrl] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [feedNotice, setFeedNotice] = useState("");
  const urlInput = useRef<HTMLInputElement | null>(null);
  const active = useMemo(() => activeItems(library.items), [library.items]);
  const runs = useActiveRuns(active.map((item) => item.processing_run_id));
  const service = blockingService(readiness);
  const queued = active.filter(
    (item) => item.library_state === EpisodeLibraryState.QUEUED
  ).length;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!url.trim()) {
      urlInput.current?.focus();
      return;
    }
    setSheetUrl(url.trim());
  };

  const copyFeed = () => {
    api
      .feedSubscription()
      .then((value) => navigator.clipboard.writeText(value.subscription_url))
      .then(() => setFeedNotice(copy.actions.feedCopied))
      .catch(() => setFeedNotice(copy.actions.feedCopyFailed));
  };

  useEffect(() => {
    if (!feedNotice) {
      return;
    }
    const timer = window.setTimeout(() => setFeedNotice(""), 2500);
    return () => window.clearTimeout(timer);
  }, [feedNotice]);

  return (
    <div className="page home-page">
      <Backdrop className="home-backdrop" />
      <header className="home-header">
        <span className="brand">{copy.brand}</span>
        <span className="header-actions">
          {feedNotice ? (
            <span className="header-notice" role="status">
              {feedNotice}
            </span>
          ) : null}
          <button
            type="button"
            className="circle-button is-large"
            aria-label={copy.actions.copyFeed}
            onClick={copyFeed}
          >
            <FeedIcon size={19} />
          </button>
          <RouteLink
            to={{ name: "settings" }}
            className="circle-button is-large has-dot-anchor"
            aria-label={
              service ? copy.actions.settingsNeedsAttention : copy.actions.settings
            }
          >
            <SlidersIcon size={19} />
            {service ? <span className="attention-dot" aria-hidden="true" /> : null}
          </RouteLink>
        </span>
      </header>

      <div className="home-grid">
        <div className="home-start">
          <h1 className="home-title">
            <b>{copy.home.titleBold}</b>
            {copy.home.titleLight}
            <br />
            {copy.home.titleSecondLight}
            <b>{copy.home.titleSecondBold}</b>
          </h1>
          <form className="add-form" onSubmit={submit}>
            <label htmlFor="episode-url" className="visually-hidden">
              {copy.home.urlLabel}
            </label>
            <input
              ref={urlInput}
              id="episode-url"
              className="pill-input"
              type="url"
              inputMode="url"
              placeholder={copy.home.urlPlaceholder}
              value={url}
              onChange={(event) => setUrl(event.target.value)}
            />
            <button type="submit" className="pill-button is-primary is-block">
              {copy.home.start}
            </button>
          </form>

          {service ? (
            <article className="service-alert" role="alert">
              <p className="service-alert-kicker">
                <AlertIcon size={14} />
                {copy.home.serviceAlert}
              </p>
              <p className="service-alert-title">
                {copy.settings.services[service] ?? service} ·{" "}
                {copy.settings.missing}
              </p>
              <p className="service-alert-body">{copy.home.serviceImpact}</p>
              <RouteLink to={{ name: "settings" }} className="pill-button is-light">
                {copy.home.serviceFix}
              </RouteLink>
            </article>
          ) : null}

          <div className="active-heading">
            <MixedHeading
              bold={copy.home.activeTitle}
              light={` ${active.length}`}
              className="section-heading"
            />
            {queued ? (
              <span className="muted-note">{copy.home.queuedCount(queued)}</span>
            ) : null}
          </div>
          {active.length ? (
            <ul className="active-list">
              {active.map((item) => (
                <ActiveRow
                  key={item.processing_run_id}
                  item={item}
                  run={runs[item.processing_run_id]}
                />
              ))}
            </ul>
          ) : (
            <p className="muted-paragraph">
              {library.loading ? copy.home.loading : copy.home.activeEmpty}
            </p>
          )}

          <MiniPlayer />
        </div>

        <LibraryPanel />
      </div>

      {sheetUrl !== null ? (
        <AddSheet initialUrl={sheetUrl} onClose={() => setSheetUrl(null)} />
      ) : null}
    </div>
  );
}

function ActiveRow({ item, run }: { item: ApiLibraryItem; run?: ApiRun }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(timer);
  }, []);
  let status: ReactNode;
  if (item.library_state === EpisodeLibraryState.ACTION_REQUIRED) {
    const audit = run?.current_error?.kind === "listening_audit"
      ? stepAudit(
          run.pipeline_steps.find((step) => step.id === "publication_audio_quality")
        )
      : null;
    status = (
      <span className="active-status is-attention">
        <span className="status-dot" aria-hidden="true" />
        {audit
          ? `${copy.activeRow.actionRequired} · ${copy.activeRow.listeningAudit(
              audit.blocking_unit_ids.length
            )}`
          : copy.activeRow.failed}
      </span>
    );
  } else if (item.library_state === EpisodeLibraryState.READY_TO_START) {
    status = <span className="active-status">{copy.activeRow.readyToStart}</span>;
  } else if (item.library_state === EpisodeLibraryState.QUEUED) {
    status = <span className="active-status">{copy.activeRow.queued}</span>;
  } else if (run) {
    const done = finishedSteps(run);
    const total = run.pipeline_steps.length;
    const step = currentStep(run);
    const elapsed = runElapsedMs(run, now);
    status = (
      <>
        <span className="step-bars" aria-hidden="true">
          {run.pipeline_steps.map((pipelineStep, index) => (
            <span
              key={pipelineStep.id}
              className={cn(index < done && "is-done", index === done && "is-current")}
            />
          ))}
        </span>
        <span className="active-status is-small">
          {copy.activeRow.processing(
            stepLabel(step?.id ?? ""),
            Math.min(done + 1, total),
            total
          )}
          {elapsed !== null
            ? ` · ${copy.activeRow.elapsed(formatElapsed(elapsed))}`
            : ""}
        </span>
      </>
    );
  } else {
    status = <span className="active-status">{copy.run.processingTitle}</span>;
  }
  return (
    <li>
      <RouteLink to={routeForItem(item)} className="active-row">
        <Artwork src={item.artwork_url} className="art-56" />
        <span className="active-text">
          <span className="row-title">{item.title}</span>
          {status}
        </span>
        <ChevronRightIcon size={16} className="row-chevron" />
      </RouteLink>
    </li>
  );
}

function MiniPlayer() {
  const player = usePlayer();
  if (!player.track) {
    return null;
  }
  const { track } = player;
  const duration = player.duration || track.duration;
  const progress = duration > 0 ? Math.min(1, player.currentTime / duration) : 0;
  return (
    <section className="mini-player" aria-label={copy.home.resume}>
      <Artwork src={track.artworkUrl} className="art-56" />
      <RouteLink
        to={{ name: "episode", id: track.episodeId }}
        className="mini-player-text"
      >
        <span className="mini-player-kicker">
          {player.playing ? copy.home.nowPlaying : copy.home.resume}
        </span>
        <span className="mini-player-title">{track.title}</span>
        <span className="mini-player-progress">
          <span className="mini-player-bar" aria-hidden="true">
            <span style={{ width: `${progress * 100}%` }} />
          </span>
          <span className="mini-player-time">
            {formatClock(player.currentTime)} / {formatClock(duration)}
          </span>
        </span>
      </RouteLink>
      <SpeedCycle rate={player.rate} onCycle={player.cycleRate} />
      <button
        type="button"
        className="mini-player-toggle"
        aria-label={player.playing ? copy.actions.pause : copy.actions.play}
        onClick={player.toggle}
      >
        {player.playing ? <PauseIcon size={18} /> : <PlayIcon size={18} />}
      </button>
    </section>
  );
}

function LibraryPanel() {
  const library = useLibrary();
  const player = usePlayer();
  const [tab, setTab] = useState<Tab>("ready");
  const [searching, setSearching] = useState(false);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState<number | null>(null);
  const [notice, setNotice] = useState("");

  const groups = useMemo(() => {
    const ready = library.items
      .filter((item) => item.library_state === EpisodeLibraryState.READY_TO_PUBLISH)
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
    const published = library.items
      .filter((item) => item.library_state === EpisodeLibraryState.PUBLISHED)
      .sort((a, b) =>
        (b.published_at ?? "").localeCompare(a.published_at ?? "")
      );
    const removed = library.items
      .filter((item) =>
        [EpisodeLibraryState.TAKEN_DOWN, EpisodeLibraryState.UNPUBLISHED].includes(
          item.library_state
        )
      )
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
    return { ready, published, removed };
  }, [library.items]);

  const needle = query.trim().toLocaleLowerCase();
  const rows = groups[tab].filter(
    (item) =>
      !needle ||
      `${item.title} ${item.source_show} ${item.source_title}`
        .toLocaleLowerCase()
        .includes(needle)
  );
  const tabs: Array<{ id: Tab; label: string; count: number }> = [
    { id: "ready" as const, label: copy.home.ready, count: groups.ready.length },
    {
      id: "published" as const,
      label: copy.home.published,
      count: groups.published.length
    },
    { id: "removed" as const, label: copy.home.removed, count: groups.removed.length }
  ].filter((item) => item.id !== "removed" || item.count > 0 || tab === "removed");

  const decide = (
    item: ApiLibraryItem,
    decision: PublicationDecisionRequest.decision
  ) => {
    if (item.episode_id === null) {
      return;
    }
    setBusy(item.episode_id);
    setNotice("");
    api
      .decidePublication(item.episode_id, decision, item.state)
      .then(() => library.refresh())
      .catch((reason: unknown) =>
        setNotice(`${copy.episode.publishFailed}：${errorMessage(reason)}`)
      )
      .finally(() => setBusy(null));
  };

  const emptyText = needle
    ? copy.home.emptySearch
    : library.loading
      ? copy.home.loading
      : tab === "ready"
        ? copy.home.emptyReady
        : tab === "published"
          ? copy.home.emptyPublished
          : copy.home.emptyRemoved;

  return (
    <section className="panel library-panel" aria-labelledby="library-title">
      <div className="library-head">
        <MixedHeading
          bold={copy.home.libraryBold}
          light={copy.home.libraryLight}
          id="library-title"
          className="panel-title"
        />
        {searching ? (
          <span className="library-search">
            <input
              className="pill-input is-compact"
              type="search"
              aria-label={copy.actions.search}
              placeholder={copy.home.searchPlaceholder}
              value={query}
              autoFocus
              onChange={(event) => setQuery(event.target.value)}
            />
            <button
              type="button"
              className="circle-button"
              aria-label={copy.actions.closeSearch}
              onClick={() => {
                setSearching(false);
                setQuery("");
              }}
            >
              <CloseIcon size={16} />
            </button>
          </span>
        ) : (
          <button
            type="button"
            className="circle-button"
            aria-label={copy.actions.search}
            onClick={() => setSearching(true)}
          >
            <SearchIcon />
          </button>
        )}
      </div>
      <div className="chip-group" role="group" aria-label={copy.home.filterLabel}>
        {tabs.map((item) => (
          <button
            key={item.id}
            type="button"
            className={cn("chip", tab === item.id && "is-selected")}
            aria-pressed={tab === item.id}
            onClick={() => setTab(item.id)}
          >
            {item.label} {item.count}
          </button>
        ))}
      </div>
      {library.error && !library.items.length ? (
        <p className="muted-paragraph" role="alert">
          {copy.home.loadError}
        </p>
      ) : null}
      {rows.length ? (
        <ul className="library-list">
          {rows.map((item) => {
            const track = trackForItem(item);
            const loaded = sameTrack(player.track, track);
            const playingThis = loaded && player.playing;
            const togglePlay = () => {
              if (!track) {
                return;
              }
              if (loaded) {
                player.toggle();
              } else {
                player.play(track);
              }
            };
            return (
              <li key={item.processing_run_id} className="library-row">
                <Artwork src={item.artwork_url} className="art-56" />
                <RouteLink to={routeForItem(item)} className="library-text">
                  <span className="row-title">{item.title}</span>
                  <span className="row-meta">
                    {tab === "published"
                      ? `${item.source_show} · ${formatDay(item.published_at)}`
                      : `${item.source_show} · ${formatLength(item.duration_seconds)}`}
                  </span>
                </RouteLink>
                {tab === "ready" ? (
                  <span className="row-actions">
                    <button
                      type="button"
                      className="circle-button"
                      aria-label={`${playingThis ? copy.actions.pause : copy.actions.audition} ${item.title}`}
                      onClick={togglePlay}
                    >
                      {playingThis ? <PauseIcon size={13} /> : <PlayIcon size={13} />}
                    </button>
                    <button
                      type="button"
                      className="pill-button is-primary is-small"
                      disabled={busy !== null}
                      onClick={() =>
                        decide(item, PublicationDecisionRequest.decision.PUBLISH)
                      }
                    >
                      {busy === item.episode_id ? copy.actions.working : copy.actions.publish}
                    </button>
                    <button
                      type="button"
                      className="text-button"
                      disabled={busy !== null}
                      onClick={() =>
                        decide(item, PublicationDecisionRequest.decision.DO_NOT_PUBLISH)
                      }
                    >
                      {copy.actions.decline}
                    </button>
                  </span>
                ) : tab === "published" ? (
                  <span className="row-actions">
                    <span className="row-meta is-inline">
                      {formatLength(item.duration_seconds)}
                    </span>
                    <button
                      type="button"
                      className="circle-button"
                      aria-label={`${playingThis ? copy.actions.pause : copy.actions.play} ${item.title}`}
                      onClick={togglePlay}
                    >
                      {playingThis ? <PauseIcon size={13} /> : <PlayIcon size={13} />}
                    </button>
                  </span>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="muted-paragraph">{emptyText}</p>
      )}
      {notice ? (
        <p className="panel-notice" role="alert">
          {notice}
        </p>
      ) : null}
    </section>
  );
}
