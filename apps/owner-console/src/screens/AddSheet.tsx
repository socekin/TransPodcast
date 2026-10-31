import { type FormEvent, useEffect, useRef, useState } from "react";

import { Artwork, RingArt } from "../components/Art";
import { MixedHeading } from "../components/Controls";
import { AlertIcon, CloseIcon } from "../components/Icons";
import { copy } from "../i18n/zh-CN";
import { api, type ApiLibraryItem, type ApiSourceResolution } from "../lib/api";
import {
  errorMessage,
  formatDay,
  formatLength,
  youtubeVideoId
} from "../lib/format";
import { EpisodeLibraryState } from "../lib/generated";
import { routeForItem, useLibrary } from "../lib/library";
import { useNavigate } from "../lib/routing";
import { youtubeThumbnail } from "../lib/utils";

type Phase =
  | { name: "resolving" }
  | { name: "resolved"; resolution: ApiSourceResolution }
  | { name: "existing"; item: ApiLibraryItem }
  | { name: "channel" }
  | { name: "error"; message: string }
  | { name: "starting"; resolution: ApiSourceResolution };

const PENDING_START = new Set(["pending_start", "ready_to_start"]);

function existingItem(
  items: ApiLibraryItem[],
  videoId: string
): ApiLibraryItem | undefined {
  return items.find((item) => youtubeVideoId(item.source_url) === videoId);
}

function stateLine(item: ApiLibraryItem): string {
  switch (item.library_state) {
    case EpisodeLibraryState.PUBLISHED:
      return `${copy.home.published} · ${formatDay(item.published_at)}`;
    case EpisodeLibraryState.READY_TO_PUBLISH:
      return copy.home.ready;
    case EpisodeLibraryState.CLOSED:
      return copy.run.closedTitle;
    case EpisodeLibraryState.TAKEN_DOWN:
    case EpisodeLibraryState.UNPUBLISHED:
      return copy.home.removed;
    default:
      return copy.home.activeTitle;
  }
}

export function AddSheet({
  initialUrl,
  onClose
}: {
  initialUrl: string;
  onClose: () => void;
}) {
  const library = useLibrary();
  const navigate = useNavigate();
  const [url, setUrl] = useState(initialUrl);
  // A new attempt re-resolves even when the URL is unchanged.
  const [request, setRequest] = useState({ url: initialUrl, attempt: 0 });
  const resolvedUrl = request.url;
  const [phase, setPhase] = useState<Phase>({ name: "resolving" });
  const [startError, setStartError] = useState("");
  const dialog = useRef<HTMLElement | null>(null);
  const itemsRef = useRef(library.items);
  itemsRef.current = library.items;

  useEffect(() => {
    setStartError("");
    // A link to an episode already in the library needs no lookup.
    const knownId = youtubeVideoId(request.url);
    const known = knownId ? existingItem(itemsRef.current, knownId) : undefined;
    if (known) {
      setPhase({ name: "existing", item: known });
      return;
    }
    const controller = new AbortController();
    setPhase({ name: "resolving" });
    api
      .resolveSource(request.url, controller.signal)
      .then((resolution) => {
        const video = resolution.requested_video;
        if (!video) {
          setPhase({ name: "channel" });
          return;
        }
        const existing = existingItem(itemsRef.current, video.youtube_video_id);
        setPhase(
          existing
            ? { name: "existing", item: existing }
            : { name: "resolved", resolution }
        );
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setPhase({ name: "error", message: errorMessage(reason) });
        }
      });
    return () => controller.abort();
  }, [request]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    dialog.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const start = (resolution: ApiSourceResolution) => {
    setPhase({ name: "starting", resolution });
    setStartError("");
    api
      .importResolvedVideo(resolution.resolution_id)
      .then(async (imported) => {
        const runId = imported.processing_run_id;
        if (runId !== null && PENDING_START.has(imported.run_status ?? "")) {
          await api.startRun(runId);
        }
        await library.refresh();
        onClose();
        if (imported.episode_id !== null && imported.reused) {
          navigate({ name: "episode", id: imported.episode_id });
        } else if (runId !== null) {
          navigate({ name: "run", id: runId });
        }
      })
      .catch((reason: unknown) => {
        setStartError(`${copy.add.startFailed}：${errorMessage(reason)}`);
        setPhase({ name: "resolved", resolution });
      });
  };

  const urlChanged = url.trim() !== resolvedUrl;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (urlChanged || phase.name === "error" || phase.name === "channel") {
      if (url.trim()) {
        setRequest((current) => ({
          url: url.trim(),
          attempt: current.attempt + 1
        }));
      }
      return;
    }
    if (phase.name === "resolved") {
      start(phase.resolution);
    }
  };

  const resolution =
    phase.name === "resolved" || phase.name === "starting"
      ? phase.resolution
      : null;
  const video = resolution?.requested_video ?? null;
  const needsResolve =
    urlChanged || phase.name === "error" || phase.name === "channel";
  const primaryLabel = needsResolve
    ? copy.add.retry
    : phase.name === "starting"
      ? copy.add.starting
      : copy.add.start;
  const primaryDisabled =
    phase.name === "resolving" ||
    phase.name === "starting" ||
    (phase.name === "existing" && !urlChanged);

  return (
    <div
      className="sheet-backdrop"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <section
        ref={dialog}
        className="sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-sheet-title"
        tabIndex={-1}
      >
        <div className="sheet-head">
          <MixedHeading
            bold={copy.add.titleBold}
            light={copy.add.titleLight}
            as="h1"
            id="add-sheet-title"
            className="sheet-title"
          />
          <button
            type="button"
            className="circle-button"
            aria-label={copy.actions.close}
            onClick={onClose}
          >
            <CloseIcon size={16} />
          </button>
        </div>

        {phase.name === "resolving" ? (
          <div className="sheet-preview is-loading" aria-live="polite">
            <RingArt progress={null}>
              <span className="ring-art-image is-placeholder" />
            </RingArt>
            <p className="muted-note">{copy.add.resolving}</p>
          </div>
        ) : null}

        {video ? (
          <div className="sheet-preview">
            <RingArt progress={null}>
              <Artwork
                className="ring-art-image"
                src={youtubeThumbnail(video.youtube_video_id)}
                alt=""
              />
            </RingArt>
            <div className="sheet-video">
              <p className="sheet-video-title">{video.title}</p>
              <p className="muted-note">
                {copy.add.meta(
                  resolution?.channel.title ?? "",
                  formatLength(video.duration_seconds)
                )}
              </p>
            </div>
          </div>
        ) : null}

        {phase.name === "existing" ? (
          <>
            <p className="sheet-message" id="add-sheet-help">
              <AlertIcon size={18} />
              <span>
                <b>{copy.add.existing}</b>
                {copy.add.existingNote}
              </span>
            </p>
            <button
              type="button"
              className="existing-card"
              onClick={() => {
                onClose();
                navigate(routeForItem(phase.item));
              }}
            >
              <Artwork src={phase.item.artwork_url} className="art-56" />
              <span className="existing-text">
                <span className="row-title">{phase.item.title}</span>
                <span className="row-meta">{stateLine(phase.item)}</span>
              </span>
              <span className="pill-button is-outline is-small">
                {copy.add.open}
              </span>
            </button>
          </>
        ) : null}

        {phase.name === "channel" || phase.name === "error" ? (
          <p className="sheet-message" role="alert">
            <AlertIcon size={18} />
            <span>
              {phase.name === "channel" ? (
                copy.add.channelLink
              ) : (
                <>
                  <b>{copy.add.resolveFailed}</b> {phase.message}
                </>
              )}
            </span>
          </p>
        ) : null}

        <form className="sheet-form" onSubmit={submit}>
          <label htmlFor="sheet-url" className="visually-hidden">
            {copy.home.urlLabel}
          </label>
          <input
            id="sheet-url"
            className="pill-input is-inset"
            type="url"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            aria-describedby={
              phase.name === "existing" ? "add-sheet-help" : undefined
            }
          />
          <button
            type="submit"
            className="pill-button is-primary is-block"
            disabled={primaryDisabled}
          >
            {primaryLabel}
          </button>
        </form>
        {startError ? (
          <p className="sheet-error" role="alert">
            {startError}
          </p>
        ) : null}
        {phase.name === "resolved" || phase.name === "starting" ? (
          <p className="muted-note sheet-note">{copy.add.note}</p>
        ) : null}
      </section>
    </div>
  );
}
