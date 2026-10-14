import { Dialog } from "@base-ui/react/dialog";
import { Menu } from "@base-ui/react/menu";
import { Tabs } from "@base-ui/react/tabs";
import {
  ArrowLeft,
  ArrowUpRight,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Clock3,
  FileText,
  Languages,
  ListMusic,
  MoreHorizontal,
  Pause,
  Play,
  Plus,
  Radio,
  RotateCcw,
  Search,
  Send,
  ShieldCheck,
  UserRound,
  X
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { copy } from "../i18n/zh-CN";
import type { View } from "../components/Shell";
import {
  ListeningAuditFindingList,
  ListeningAuditPanel
} from "../components/ListeningAudit";
import {
  Badge,
  Button,
  Card,
  AsyncResourceState
} from "../components/ui";
import {
  api,
  asListeningAudit,
  type ApiDirectSourceVideoImport,
  type ApiEpisodeDetail,
  type ApiEpisodeLibraryItem,
  type ApiPublicationPresentationField,
  type ApiProcessingDetail,
  type ApiProcessingQueueItem,
  type ApiSettings,
  type ApiSourceResolution
} from "../lib/api";
import { usePlayer } from "../lib/player";
import {
  EpisodeLibraryAction,
  PublicationDecisionRequest
} from "../lib/generated";
import { useApiResource } from "../lib/useApiResource";
import { DEFAULT_ARTWORK_URL } from "../lib/utils";

type EpisodeRow = {
  episodeId: number | null;
  processingRunId: number | null;
  show: string;
  title: string;
  overview: string;
  state: string;
  statusTone: "success" | "primary" | "warning" | "neutral";
  bucket:
    | "published"
    | "processing"
    | "ready"
    | "pending"
    | "taken_down"
    | "other";
  time: string;
  context: string;
  libraryState: ApiEpisodeLibraryItem["library_state"];
  rawState: string;
  actions: ApiEpisodeLibraryItem["available_actions"];
  youtubeVideoId?: string | null;
  runStatus?: string | null;
  artworkUrl?: string;
};

type LibraryConfirmation = {
  kind: "publish" | "decline" | "remove";
  row: EpisodeRow;
};

type PublicationMetadataDraft = {
  description: string;
  chapters: Array<{ time: string; title: string }>;
};

const EMPTY_PUBLICATION_METADATA_DRAFT: PublicationMetadataDraft = {
  description: "",
  chapters: []
};

export function EpisodeLibrary({
  onNavigate,
  onOpenEpisode,
  onOpenRun,
  decisionCount
}: {
  onNavigate: (view: View) => void;
  onOpenEpisode?: (episodeId: number | null) => void;
  onOpenRun?: (runId: number | null) => void;
  decisionCount: number | null;
}) {
  const [notice, setNotice] = useState("");
  const [reusedImport, setReusedImport] =
    useState<ApiDirectSourceVideoImport | null>(null);
  const [addEpisodeOpen, setAddEpisodeOpen] = useState(false);
  const [episodeUrl, setEpisodeUrl] = useState("");
  const [episodeResolution, setEpisodeResolution] =
    useState<ApiSourceResolution | null>(null);
  const [episodeResolving, setEpisodeResolving] = useState(false);
  const [episodeImporting, setEpisodeImporting] = useState(false);
  const [episodeImportError, setEpisodeImportError] = useState("");
  const episodeResolveController = useRef<AbortController | null>(null);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [confirmation, setConfirmation] =
    useState<LibraryConfirmation | null>(null);
  const [mutatingRowKeys, setMutatingRowKeys] = useState<Set<string>>(
    () => new Set()
  );
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const player = usePlayer();
  const loadLibrary = useCallback(
    (signal: AbortSignal) => api.episodeLibrary(signal),
    []
  );
  const resource = useApiResource(loadLibrary);
  const loadQueue = useCallback(
    (signal: AbortSignal) => api.processingQueue(signal),
    []
  );
  const queue = useApiResource<{ items: ApiProcessingQueueItem[] }>(loadQueue);
  const loadSettings = useCallback(
    (signal: AbortSignal) => api.settings(signal),
    []
  );
  const settings = useApiResource<ApiSettings | null>(loadSettings);
  const queueItems =
    queue.status === "ready" ? (queue.data?.items ?? []) : [];
  const manualMode =
    settings.status === "ready" && settings.data
      ? settings.data.automation.manual_processing
      : null;
  const hasPendingStart = queueItems.some(
    (item) => item.status === "pending_start"
  );
  const resetEpisodeImport = () => {
    episodeResolveController.current?.abort();
    episodeResolveController.current = null;
    setEpisodeUrl("");
    setEpisodeResolution(null);
    setEpisodeResolving(false);
    setEpisodeImporting(false);
    setEpisodeImportError("");
  };
  const resolveEpisode = () => {
    const value = episodeUrl.trim();
    if (!value) {
      setEpisodeImportError(copy.library.videoUrlRequired);
      return;
    }
    setEpisodeResolving(true);
    setEpisodeResolution(null);
    setEpisodeImportError("");
    episodeResolveController.current?.abort();
    const controller = new AbortController();
    episodeResolveController.current = controller;
    api
      .resolveSource(value, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) {
          return;
        }
        if (!result.requested_video) {
          throw new Error(copy.library.videoUrlRequired);
        }
        setEpisodeResolution(result);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setEpisodeImportError(
          reason instanceof Error ? reason.message : String(reason)
        );
      })
      .finally(() => {
        if (episodeResolveController.current === controller) {
          episodeResolveController.current = null;
          setEpisodeResolving(false);
        }
      });
  };
  const importEpisode = () => {
    if (!episodeResolution || manualMode === null) {
      return;
    }
    setEpisodeImporting(true);
    setEpisodeImportError("");
    api
      .importResolvedVideo(episodeResolution.resolution_id)
      .then((result) => {
        setReusedImport(result.reused ? result : null);
        setNotice(
          result.reused
            ? copy.library.reusedEpisode
            : copy.library.importedEpisode
        );
        setAddEpisodeOpen(false);
        queue.reload();
        resource.reload();
      })
      .catch((reason: unknown) =>
        setEpisodeImportError(
          reason instanceof Error ? reason.message : String(reason)
        )
      )
      .finally(() => setEpisodeImporting(false));
  };
  const reloadLibrary = () => {
    queue.reload();
    resource.reload();
  };
  const runRowMutation = (
    row: EpisodeRow,
    operation: () => Promise<unknown>,
    onSuccess: () => void
  ) => {
    const key = episodeRowKey(row);
    setMutatingRowKeys((current) => new Set(current).add(key));
    setRowErrors((current) => ({ ...current, [key]: "" }));
    operation()
      .then(() => {
        onSuccess();
        reloadLibrary();
      })
      .catch((reason: unknown) =>
        setRowErrors((current) => ({
          ...current,
          [key]: reason instanceof Error ? reason.message : String(reason)
        }))
      )
      .finally(() =>
        setMutatingRowKeys((current) => {
          const next = new Set(current);
          next.delete(key);
          return next;
        })
      );
  };
  const startRun = (row: EpisodeRow) => {
    const processingRunId = row.processingRunId;
    if (processingRunId === null) {
      return;
    }
    runRowMutation(
      row,
      () => api.startRun(processingRunId),
      () => setNotice(copy.queue.started)
    );
  };
  const startAllRuns = () => {
    api
      .startAllRuns()
      .then((result) => {
        setNotice(
          `${copy.queue.startedAllPrefix}${result.started}${copy.queue.startedAllSuffix}`
        );
        reloadLibrary();
      })
      .catch((reason: unknown) =>
        setNotice(
          reason instanceof Error ? reason.message : String(reason)
        )
      );
  };
  const restoreEpisode = (row: EpisodeRow) => {
    const episodeId = row.episodeId;
    if (episodeId === null) {
      return;
    }
    runRowMutation(
      row,
      () => api.recoverEpisode(episodeId),
      () => setNotice(copy.library.episodeRestored)
    );
  };
  const applyConfirmation = () => {
    if (!confirmation) {
      return;
    }
    const { kind, row } = confirmation;
    runRowMutation(
      row,
      () =>
        kind === "remove" && row.processingRunId !== null
          ? api.cancelRun(row.processingRunId)
          : row.episodeId !== null
            ? api.decidePublication(
                row.episodeId,
                kind === "publish"
                  ? PublicationDecisionRequest.decision.PUBLISH
                  : PublicationDecisionRequest.decision.DO_NOT_PUBLISH,
                row.rawState
              )
            : Promise.reject(new Error(copy.library.unknownState)),
      () => {
        setNotice(
          kind === "publish"
            ? copy.library.publicationSucceeded
            : kind === "decline"
              ? copy.library.publicationDeclined
              : copy.queue.removed
        );
        setConfirmation(null);
      }
    );
  };
  const rows = useMemo<EpisodeRow[]>(() => {
    if (resource.status !== "ready") {
      return [];
    }
    const queueByRunId = new Map(
      queueItems.map((item) => [item.processing_run_id, item])
    );
    return (resource.data?.items ?? []).map((item) =>
      episodeRowFromApi(item, queueByRunId.get(item.processing_run_id))
    );
  }, [queueItems, resource.data, resource.status]);
  const pendingCount = useMemo(
    () => rows.filter((row) => row.bucket === "pending").length,
    [rows]
  );
  const publicationDecisionCount =
    resource.status === "ready"
      ? (resource.data?.items.filter(
          (item) => item.library_state === "ready_to_publish"
        ).length ?? 0)
      : null;
  const attentionCount =
    decisionCount === null || publicationDecisionCount === null
      ? null
      : decisionCount + publicationDecisionCount;
  const actionableDecisionCount = decisionCount ?? 0;
  const actionablePublicationCount = publicationDecisionCount ?? 0;
  const visibleRows = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return rows.filter((row) => {
      const matchesSearch =
        !needle ||
        `${row.show} ${row.title} ${row.overview}`
          .toLocaleLowerCase()
          .includes(needle);
      const matchesFilter =
        (filter === "all" && row.bucket !== "taken_down") ||
        filter === row.bucket;
      return matchesSearch && matchesFilter;
    });
  }, [filter, rows, search]);
  const openRow = (row: EpisodeRow) => {
    if (row.episodeId === null) {
      onOpenRun?.(row.processingRunId);
      onNavigate("runs");
      return;
    }
    onOpenEpisode?.(
      resource.status === "ready" ? row.episodeId : null
    );
    onNavigate("episode");
  };

  return (
    <>
      <PageHeading
        title={copy.library.title}
        description={copy.library.description}
        actions={
          <Dialog.Root
            open={addEpisodeOpen}
            onOpenChange={(open) => {
              if (!open && episodeImporting) {
                return;
              }
              setAddEpisodeOpen(open);
              if (!open) {
                resetEpisodeImport();
              }
            }}
          >
            <Dialog.Trigger className="button button-primary button-md">
              <Plus size={14} aria-hidden="true" />
              {copy.library.addEpisode}
            </Dialog.Trigger>
            <Dialog.Portal>
              <Dialog.Backdrop className="dialog-backdrop" />
              <Dialog.Viewport className="dialog-viewport">
                <Dialog.Popup className="dialog-popup add-episode-dialog">
                  <header className="dialog-header">
                    <div>
                      <Dialog.Title>{copy.library.addEpisodeTitle}</Dialog.Title>
                      <Dialog.Description>
                        {copy.library.addEpisodeDescription}
                      </Dialog.Description>
                    </div>
                    <Dialog.Close
                      aria-label={copy.common.cancel}
                      className="icon-button"
                      disabled={episodeImporting}
                    >
                      <X size={15} aria-hidden="true" />
                    </Dialog.Close>
                  </header>

                  <form
                    className="add-episode-form"
                    onSubmit={(event) => {
                      event.preventDefault();
                      if (episodeResolution) {
                        importEpisode();
                        return;
                      }
                      resolveEpisode();
                    }}
                  >
                    <label className="field">
                      <span className="field-label">
                        {copy.library.addEpisodeUrlLabel}
                      </span>
                      <div className="input-action">
                        <input
                          autoFocus
                          value={episodeUrl}
                          disabled={episodeResolving || episodeImporting}
                          placeholder={copy.library.addEpisodeUrlPlaceholder}
                          onChange={(event) => {
                            setEpisodeUrl(event.target.value);
                            setEpisodeResolution(null);
                            setEpisodeImportError("");
                          }}
                        />
                        <Button
                          tone="secondary"
                          type="button"
                          disabled={episodeResolving || episodeImporting}
                          onClick={resolveEpisode}
                        >
                          {episodeResolving
                            ? copy.library.resolvingEpisode
                            : copy.library.resolveEpisode}
                        </Button>
                      </div>
                    </label>

                    {episodeResolution?.requested_video ? (
                      <div className="direct-episode-preview">
                        <img
                          src={DEFAULT_ARTWORK_URL}
                          alt=""
                        />
                        <span>
                          <Badge tone="success">
                            {copy.library.resolvedEpisode}
                          </Badge>
                          <strong>
                            {episodeResolution.requested_video.title}
                          </strong>
                          <small>{episodeResolution.channel.title}</small>
                        </span>
                      </div>
                    ) : null}

                    {episodeResolution ? (
                      <div className="direct-import-notes">
                        <div className="direct-import-setting-status">
                          <p>
                            {manualMode === null
                              ? settings.status === "loading"
                                ? copy.library.processingSettingLoading
                                : copy.library.processingSettingUnavailable
                              : manualMode
                                ? copy.library.importManualNote
                                : copy.library.importAutomaticNote}
                          </p>
                          {settings.status !== "ready" &&
                          settings.status !== "loading" ? (
                            <Button
                              tone="ghost"
                              size="sm"
                              type="button"
                              onClick={settings.reload}
                            >
                              {copy.library.retryProcessingSetting}
                            </Button>
                          ) : null}
                        </div>
                        <p>
                          {episodeResolution.existing_source_channel
                            ? copy.library.importExistingChannelNote
                            : copy.library.importNewChannelNote}
                        </p>
                      </div>
                    ) : null}

                    {episodeImportError ? (
                      <p className="error-notice" role="alert">
                        {episodeImportError}
                      </p>
                    ) : null}

                    <footer className="dialog-actions">
                      <Dialog.Close
                        className="button button-ghost button-md"
                        disabled={episodeImporting}
                      >
                        {copy.common.cancel}
                      </Dialog.Close>
                      <Button
                        type="submit"
                        disabled={
                          !episodeResolution ||
                          episodeImporting ||
                          manualMode === null
                        }
                      >
                        {episodeImporting
                          ? copy.library.importingEpisode
                          : copy.library.importEpisode}
                      </Button>
                    </footer>
                  </form>
                </Dialog.Popup>
              </Dialog.Viewport>
            </Dialog.Portal>
          </Dialog.Root>
        }
      />
      {notice ? (
        <div className="inline-notice" role="status">
          <span>{notice}</span>
          {notice === copy.library.reusedEpisode &&
          reusedImport &&
          (reusedImport.episode_id !== null ||
            reusedImport.processing_run_id !== null) ? (
            <Button
              tone="ghost"
              size="sm"
              type="button"
              onClick={() => {
                if (reusedImport.episode_id !== null) {
                  onOpenEpisode?.(reusedImport.episode_id);
                  onNavigate("episode");
                  return;
                }
                onOpenRun?.(reusedImport.processing_run_id);
              }}
            >
              {reusedImport.episode_id !== null
                ? copy.library.openExistingEpisode
                : copy.library.openExistingRun}
            </Button>
          ) : null}
        </div>
      ) : null}

      {attentionCount !== null && attentionCount > 0 ? (
        <button
          className="attention"
          type="button"
          onClick={() => {
            if (actionableDecisionCount > 0) {
              onNavigate("decisions");
              return;
            }
            setFilter("ready");
          }}
        >
          <span className="attention-icon">
            <ListMusic size={16} aria-hidden="true" />
          </span>
          <span className="attention-copy">
            <strong>{copy.library.attentionTitleFor(attentionCount)}</strong>
            <small>
              {copy.library.attentionBodyFor(
                actionableDecisionCount,
                actionablePublicationCount
              )}
            </small>
          </span>
          <span className="attention-action">
            {actionableDecisionCount > 0
              ? copy.library.attentionCandidatesAction
              : copy.library.attentionPublicationsAction}
            <ChevronRight size={14} aria-hidden="true" />
          </span>
        </button>
      ) : null}

      <div className="library-toolbar">
        <Tabs.Root
          value={filter}
          onValueChange={(value) => setFilter(String(value))}
        >
          <Tabs.List className="segmented-tabs">
            <Tabs.Tab className="segmented-tab" value="all">
              {copy.library.all}
            </Tabs.Tab>
            <Tabs.Tab className="segmented-tab" value="pending">
              {copy.queue.title}
              {pendingCount > 0 ? ` ${pendingCount}` : ""}
            </Tabs.Tab>
            <Tabs.Tab className="segmented-tab" value="ready">
              {copy.library.ready}
            </Tabs.Tab>
            <Tabs.Tab className="segmented-tab" value="processing">
              {copy.library.processing}
            </Tabs.Tab>
            <Tabs.Tab className="segmented-tab" value="published">
              {copy.library.published}
            </Tabs.Tab>
            <Tabs.Tab className="segmented-tab" value="taken_down">
              {copy.library.takenDownFilter}
            </Tabs.Tab>
          </Tabs.List>
        </Tabs.Root>
        <label className="toolbar-search">
          <span className="sr-only">{copy.library.search}</span>
          <input
            type="search"
            value={search}
            placeholder={copy.library.search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
      </div>

      {filter === "pending" && manualMode === true ? (
        <p className="manual-banner list-banner">
          <Pause size={13} aria-hidden="true" />
          <span>{copy.queue.manualBanner}</span>
          {hasPendingStart ? (
            <Button
              tone="secondary"
              size="sm"
              onClick={startAllRuns}
            >
              <Play size={13} aria-hidden="true" />
              {copy.queue.startAll}
            </Button>
          ) : null}
        </p>
      ) : null}

      <Card className="library-list">
        <div className="list-head">
          <span>{copy.library.episode}</span>
          <span>{copy.library.state}</span>
          <span>{copy.library.duration}</span>
          <span>{copy.library.actions}</span>
        </div>
        {resource.status === "loading" ? (
          <AsyncResourceState
            kind="loading"
            className="empty-state"
            icon={<RotateCcw size={20} aria-hidden="true" />}
            title={copy.library.loadingTitle}
            detail={copy.library.loadingBody}
          />
        ) : null}
        {resource.status === "error" ? (
          <AsyncResourceState
            kind="error"
            className="empty-state"
            icon={<CircleAlert size={20} aria-hidden="true" />}
            title={copy.library.loadErrorTitle}
            detail={resource.error}
            onRetry={resource.reload}
            retryLabel={copy.common.retry}
          />
        ) : null}
        {resource.status === "ready" && visibleRows.length === 0 ? (
          <div className="empty-state">
            <Radio size={20} aria-hidden="true" />
            <strong>{copy.library.emptyTitle}</strong>
            <span>{copy.library.emptyBody}</span>
          </div>
        ) : null}
        {visibleRows.map((row) => (
          <article
            className="episode-row"
            key={
              row.episodeId !== null
                ? `episode-${row.episodeId}`
                : `run-${row.processingRunId ?? row.title}`
            }
          >
            <img
              className="episode-cover"
              src={row.artworkUrl || DEFAULT_ARTWORK_URL}
              alt=""
              onError={(event) => {
                event.currentTarget.src =
                  "/assets/transpodcast-artwork.png";
              }}
            />
            <div className="episode-copy">
              <span className="source-line">
                <Radio size={11} aria-hidden="true" />
                {row.show}
                <i />
                {row.context}
              </span>
              <h2>
                <button
                  className="episode-title-button"
                  type="button"
                  onClick={() => openRow(row)}
                >
                  {row.title}
                </button>
              </h2>
              <p>{row.overview}</p>
            </div>
            <div className="state-cell">
              <Badge tone={row.statusTone}>{row.state}</Badge>
            </div>
            <div className="duration-cell">
              <Clock3 size={13} aria-hidden="true" />
              {row.time}
            </div>
            <div className="episode-actions-cell">
              <div className="episode-row-actions">
                {row.actions.includes(EpisodeLibraryAction.START) ? (
                  <Button
                    size="sm"
                    disabled={mutatingRowKeys.has(episodeRowKey(row))}
                    onClick={() => startRun(row)}
                  >
                    {copy.queue.start}
                  </Button>
                ) : null}
                {row.actions.includes(EpisodeLibraryAction.VIEW) ? (
                  <Button tone="ghost" size="sm" onClick={() => openRow(row)}>
                    {copy.library.view}
                  </Button>
                ) : null}
                {row.actions.includes(EpisodeLibraryAction.PLAY) ? (
                  <Button
                    tone="secondary"
                    size="sm"
                    onClick={() => {
                      const libraryItem = resource.data?.items.find(
                        (item) => item.episode_id === row.episodeId
                      );
                      if (
                        libraryItem?.episode_id !== null &&
                        libraryItem?.episode_id !== undefined
                      ) {
                        player.load(
                          libraryItem.episode_id,
                          libraryItem.title,
                          libraryItem.duration_seconds,
                          row.libraryState === "published"
                            ? `/api/v1/episodes/${libraryItem.episode_id}/preview?published=true`
                            : undefined
                        );
                      }
                    }}
                  >
                    <Play size={12} aria-hidden="true" />
                    {copy.library.play}
                  </Button>
                ) : null}
                {row.actions.includes(EpisodeLibraryAction.PUBLISH) ? (
                  <Button
                    size="sm"
                    disabled={mutatingRowKeys.has(episodeRowKey(row))}
                    onClick={() => setConfirmation({ kind: "publish", row })}
                  >
                    {copy.library.publish}
                  </Button>
                ) : null}
                {row.actions.includes(EpisodeLibraryAction.RESTORE) ? (
                  <Button
                    size="sm"
                    disabled={mutatingRowKeys.has(episodeRowKey(row))}
                    onClick={() => restoreEpisode(row)}
                  >
                    {copy.library.restore}
                  </Button>
                ) : null}
                {row.actions.includes(EpisodeLibraryAction.REMOVE) ||
                row.actions.includes(EpisodeLibraryAction.DECLINE) ? (
                  <Menu.Root>
                    <Menu.Trigger
                      aria-label={copy.library.moreActions}
                      className="icon-button row-more-action"
                    >
                      <MoreHorizontal size={15} aria-hidden="true" />
                    </Menu.Trigger>
                    <Menu.Portal>
                      <Menu.Positioner align="end" sideOffset={6}>
                        <Menu.Popup className="row-action-menu">
                          {row.actions.includes(EpisodeLibraryAction.REMOVE) ? (
                            <Menu.Item
                              className="row-action-menu-item is-danger"
                              onClick={() =>
                                setConfirmation({ kind: "remove", row })
                              }
                            >
                              {copy.library.removeRun}
                            </Menu.Item>
                          ) : null}
                          {row.actions.includes(EpisodeLibraryAction.DECLINE) ? (
                            <Menu.Item
                              className="row-action-menu-item"
                              onClick={() =>
                                setConfirmation({ kind: "decline", row })
                              }
                            >
                              {copy.library.decline}
                            </Menu.Item>
                          ) : null}
                        </Menu.Popup>
                      </Menu.Positioner>
                    </Menu.Portal>
                  </Menu.Root>
                ) : null}
              </div>
              {rowErrors[episodeRowKey(row)] ? (
                <p className="row-action-error" role="alert">
                  {rowErrors[episodeRowKey(row)]}
                </p>
              ) : null}
            </div>
          </article>
        ))}
      </Card>
      <Dialog.Root
        open={confirmation !== null}
        onOpenChange={(open) => {
          if (
            !open &&
            (!confirmation ||
              !mutatingRowKeys.has(episodeRowKey(confirmation.row)))
          ) {
            setConfirmation(null);
          }
        }}
      >
        <Dialog.Portal>
          <Dialog.Backdrop className="dialog-backdrop" />
          <Dialog.Viewport className="dialog-viewport">
            <Dialog.Popup className="dialog-popup library-action-dialog">
              <header className="dialog-header">
                <div>
                  <Dialog.Title>
                    {confirmation?.kind === "publish"
                      ? copy.library.publishDialogTitle
                      : confirmation?.kind === "decline"
                        ? copy.library.declineDialogTitle
                        : copy.library.removeDialogTitle}
                  </Dialog.Title>
                  <Dialog.Description>
                    {confirmation?.kind === "publish"
                      ? confirmation.row.rawState === "revision_pending"
                        ? copy.library.publishRevisionDialogBody
                        : copy.library.publishDialogBody
                      : confirmation?.kind === "decline"
                        ? copy.library.declineDialogBody
                        : copy.library.removeDialogBody}
                  </Dialog.Description>
                </div>
                <Dialog.Close
                  aria-label={copy.common.cancel}
                  className="icon-button"
                  disabled={
                    confirmation !== null &&
                    mutatingRowKeys.has(episodeRowKey(confirmation.row))
                  }
                >
                  <X size={15} aria-hidden="true" />
                </Dialog.Close>
              </header>
              {confirmation ? (
                <div className="library-action-episode">
                  <img
                    src={
                      confirmation.row.artworkUrl ||
                      "/assets/transpodcast-artwork.png"
                    }
                    alt=""
                  />
                  <span>
                    <strong>{confirmation.row.title}</strong>
                    <small>
                      {confirmation.row.show} · {confirmation.row.time}
                    </small>
                  </span>
                </div>
              ) : null}
              <footer className="dialog-actions">
                <Dialog.Close
                  className="button button-ghost button-md"
                  disabled={
                    confirmation !== null &&
                    mutatingRowKeys.has(episodeRowKey(confirmation.row))
                  }
                >
                  {copy.common.cancel}
                </Dialog.Close>
                <Button
                  disabled={
                    confirmation !== null &&
                    mutatingRowKeys.has(episodeRowKey(confirmation.row))
                  }
                  onClick={applyConfirmation}
                >
                  {confirmation?.kind === "publish"
                    ? copy.library.publish
                    : confirmation?.kind === "decline"
                      ? copy.library.decline
                      : copy.library.removeRun}
                </Button>
              </footer>
            </Dialog.Popup>
          </Dialog.Viewport>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}

export function EpisodeDetail({
  onNavigate,
  onPublicationEditDirtyChange,
  episodeId
}: {
  onNavigate: (view: View) => void;
  onPublicationEditDirtyChange?: (dirty: boolean) => void;
  episodeId?: number | null;
}) {
  const [notice, setNotice] = useState("");
  const [reviewSearch, setReviewSearch] = useState("");
  const [reviewTab, setReviewTab] = useState<
    "script" | "metadata" | "processing"
  >(
    "script"
  );
  const [publicationDraft, setPublicationDraft] =
    useState<PublicationMetadataDraft>(EMPTY_PUBLICATION_METADATA_DRAFT);
  const [savingMetadata, setSavingMetadata] = useState(false);
  const [publicationError, setPublicationError] = useState("");
  const [editRefreshPhase, setEditRefreshPhase] = useState<
    "idle" | "waiting_loading" | "waiting_ready"
  >("idle");
  const [editingPublicationMetadata, setEditingPublicationMetadata] =
    useState(false);
  const [processingFilter, setProcessingFilter] = useState<
    "all" | "disputed" | "auto_corrected" | "archived"
  >("all");
  const player = usePlayer();
  const loadEpisode = useCallback(
    (signal: AbortSignal) =>
      episodeId
        ? api.episode(episodeId, signal)
        : Promise.reject(new Error("Translated Episode ID is required")),
    [episodeId]
  );
  const episode = useApiResource<ApiEpisodeDetail | null>(loadEpisode);
  const realEpisode = episode.status === "ready" ? episode.data : null;
  const publicationPresentation = realEpisode?.publication_presentation ?? {
    state: "draft" as const,
    revision_kind: "none" as const,
    changed_fields: [] as ApiPublicationPresentationField[],
    backfill_available: false,
    degraded_fields: []
  };
  useEffect(() => {
    if (!realEpisode) {
      return;
    }
    setPublicationDraft(publicationDraftForEpisode(realEpisode));
  }, [realEpisode]);
  useEffect(() => {
    if (editRefreshPhase === "waiting_loading" && episode.status === "loading") {
      setEditRefreshPhase("waiting_ready");
      return;
    }
    if (editRefreshPhase !== "waiting_ready") {
      return;
    }
    if (episode.status === "ready") {
      if (episode.data?.publication_presentation.state === "revision_pending") {
        setPublicationError(copy.episode.metadataRevisionAlreadyPending);
        setEditingPublicationMetadata(false);
      } else {
        setEditingPublicationMetadata(true);
      }
      setEditRefreshPhase("idle");
      return;
    }
    if (episode.status === "error") {
      setPublicationError(episode.error);
      setEditRefreshPhase("idle");
    }
  }, [editRefreshPhase, episode.data, episode.error, episode.status]);
  const loadProcessingDetail = useCallback(
    (signal: AbortSignal) =>
      episodeId
        ? api.processingDetail(episodeId, signal)
        : Promise.reject(new Error("Translated Episode ID is required")),
    [episodeId]
  );
  const processing = useApiResource<ApiProcessingDetail | null>(
    loadProcessingDetail
  );
  const processingDetail =
    processing.status === "ready" ? processing.data : null;
  const parsedChapterTimes = useMemo(
    () => publicationDraft.chapters.map((chapter) => parseClock(chapter.time)),
    [publicationDraft.chapters]
  );
  const chapterTimesValid = parsedChapterTimes.every(
    (value): value is number => value !== null
  );
  const chapterValidationMessage = useMemo(() => {
    if (!chapterTimesValid) {
      return copy.episode.chapterTimeInvalid;
    }
    if (publicationDraft.chapters.length === 0) {
      return "";
    }
    if (publicationDraft.chapters.length < 3) {
      return copy.episode.chapterMinimumCount;
    }
    if (parsedChapterTimes[0] !== 0) {
      return copy.episode.chapterMustStartAtZero;
    }
    if (publicationDraft.chapters.some((chapter) => !chapter.title.trim())) {
      return copy.episode.chapterTitleInvalid;
    }
    for (let index = 1; index < parsedChapterTimes.length; index += 1) {
      const previous = parsedChapterTimes[index - 1] ?? 0;
      const current = parsedChapterTimes[index] ?? 0;
      if (current <= previous) {
        return copy.episode.chapterOrderInvalid;
      }
      if (current - previous < 120) {
        return copy.episode.chapterSpacingInvalid;
      }
    }
    if (
      parsedChapterTimes.some(
        (start) => start !== null && start >= (realEpisode?.duration_seconds ?? 0)
      )
    ) {
      return copy.episode.chapterDurationInvalid;
    }
    if (
      publicationDraft.chapters.length >
      Math.floor(((realEpisode?.duration_seconds ?? 0) * 6) / 3600)
    ) {
      return copy.episode.chapterDensityInvalid;
    }
    return "";
  }, [
    publicationDraft.chapters,
    chapterTimesValid,
    parsedChapterTimes,
    realEpisode?.duration_seconds
  ]);
  const publicationMetadataDirty = useMemo(() => {
    if (!realEpisode) {
      return false;
    }
    const normalizeDescription = (value: string) =>
      value
        .trim()
        .split(/\n\s*\n/)
        .map((paragraph) => paragraph.trim())
        .filter(Boolean)
        .join("\n\n");
    const normalizeChapters = (
      chaptersToNormalize: Array<{
        start_seconds: number;
        title: string;
      }>
    ) =>
      chaptersToNormalize.map((chapter) => ({
        start_seconds: Math.floor(chapter.start_seconds),
        title: chapter.title.trim()
      }));
    return (
      normalizeDescription(publicationDraft.description) !==
        normalizeDescription(realEpisode.publication_description) ||
      JSON.stringify(
        normalizeChapters(
          publicationDraft.chapters.map((chapter, index) => ({
            start_seconds: parsedChapterTimes[index] ?? 0,
            title: chapter.title
          }))
        )
      ) !==
        JSON.stringify(normalizeChapters(realEpisode.chapters))
    );
  }, [
    publicationDraft,
    parsedChapterTimes,
    realEpisode
  ]);
  useEffect(() => {
    if (!editingPublicationMetadata || !publicationMetadataDirty) {
      return;
    }
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [editingPublicationMetadata, publicationMetadataDirty]);
  useEffect(() => {
    onPublicationEditDirtyChange?.(
      editingPublicationMetadata && publicationMetadataDirty
    );
    return () => onPublicationEditDirtyChange?.(false);
  }, [
    editingPublicationMetadata,
    onPublicationEditDirtyChange,
    publicationMetadataDirty
  ]);
  const listenerPublishedMetadata =
    publicationPresentation.state === "revision_pending"
      ? realEpisode?.published_metadata
      : null;
  const listenerDurationSeconds =
    listenerPublishedMetadata?.duration_seconds ??
    realEpisode?.duration_seconds ??
    0;
  const listenerChapters =
    publicationPresentation.state === "revision_pending" &&
    realEpisode?.published_metadata
      ? realEpisode.published_metadata.chapters
      : realEpisode?.chapters;
  const chapters = listenerChapters
    ? listenerChapters.map((chapter, index) => [
        chapter.title,
        "",
        formatClock(chapter.start_seconds),
        index + 1 < listenerChapters.length
          ? formatClock(
              listenerChapters[index + 1].start_seconds -
                chapter.start_seconds
            )
          : formatClock(
              listenerDurationSeconds - chapter.start_seconds
            )
      ])
    : [];
  const title =
    listenerPublishedMetadata?.title ?? realEpisode?.title ?? "";
  const overview =
    listenerPublishedMetadata?.overview ??
    realEpisode?.overview ??
    "";
  const publicationDescription =
    listenerPublishedMetadata?.description ??
    realEpisode?.publication_description ??
    overview;
  const sourceShow = realEpisode?.source_show ?? "";
  const sourceTitle = realEpisode?.source_title ?? "";
  const duration = realEpisode
    ? formatListenerDuration(listenerDurationSeconds)
    : "";
  const listenerAudioUrl = realEpisode
    ? `/api/v1/episodes/${realEpisode.id}/preview${
        listenerPublishedMetadata ? "?published=true" : ""
      }`
    : "";
  const episodeDate = realEpisode
    ? formatEpisodeDate(realEpisode.source_published_at)
    : "";
  const transcriptSegments = realEpisode?.segments ?? [];
  const normalizedReviewSearch = reviewSearch.trim().toLocaleLowerCase();
  const visibleReviewSegments = transcriptSegments.filter((segment) =>
    `${segment.source_text} ${segment.target_text}`
      .toLocaleLowerCase()
      .includes(normalizedReviewSearch)
  );
  const meaningfulChapters = chapters.filter(
    ([chapterTitle]) => !isGenericChapterTitle(chapterTitle)
  );
  const qualityReady =
    realEpisode?.quality_gates.eligible !== false;
  const episodeAudit = asListeningAudit(
    realEpisode?.quality_gates.listening_audit
  );
  const recoveryExpired =
    realEpisode?.state === "deleted" &&
    realEpisode.recoverable === false;
  const isLoadedEpisode = realEpisode
    ? player.episodeId === realEpisode.id &&
      player.sourceUrl === listenerAudioUrl
    : player.title === title;
  const guestNames = realEpisode?.guests ?? [];
  const toggleEpisodePlayback = () => {
    if (isLoadedEpisode) {
      player.toggle();
      return;
    }
    player.load(
      realEpisode?.id ?? null,
      title,
      listenerDurationSeconds || 5284,
      listenerAudioUrl || undefined
    );
  };

  const sendPublicationReview = () => {
    if (!realEpisode) {
      setNotice(copy.episode.sendPublishSuccess);
      return;
    }
    api
      .notifyPublication(realEpisode.id)
      .then(() => setNotice(copy.episode.sendPublishSuccess))
      .catch((reason: unknown) =>
        setNotice(
          reason instanceof Error ? reason.message : String(reason)
        )
      );
  };

  const takeDownEpisode = () => {
    if (
      !realEpisode ||
      !window.confirm(copy.episode.takeDownConfirm)
    ) {
      return;
    }
    api
      .takeDownEpisode(realEpisode.id)
      .then(() => {
        setNotice(copy.episode.takeDownSuccess);
        episode.reload();
      })
      .catch(failureNotice);
  };

  const recoverEpisode = () => {
    if (
      !realEpisode ||
      !window.confirm(copy.episode.recoverConfirm)
    ) {
      return;
    }
    api
      .recoverEpisode(realEpisode.id)
      .then(() => {
        setNotice(copy.episode.recoverSuccess);
        episode.reload();
      })
      .catch(failureNotice);
  };

  const failureNotice = (reason: unknown) =>
    setNotice(reason instanceof Error ? reason.message : String(reason));

  const resetPublicationMetadataEditor = () => {
    setPublicationDraft(
      realEpisode
        ? publicationDraftForEpisode(realEpisode)
        : EMPTY_PUBLICATION_METADATA_DRAFT
    );
    setPublicationError("");
    setEditingPublicationMetadata(false);
  };

  const beginPublicationMetadataEdit = () => {
    setPublicationError("");
    setEditRefreshPhase("waiting_loading");
    episode.reload();
  };

  const selectReviewTab = (
    nextTab: "script" | "metadata" | "processing"
  ) => {
    if (
      reviewTab === "metadata" &&
      nextTab !== "metadata" &&
      editingPublicationMetadata &&
      publicationMetadataDirty
    ) {
      if (!window.confirm(copy.episode.discardPublicationMetadataConfirm)) {
        return;
      }
      resetPublicationMetadataEditor();
    }
    setReviewTab(nextTab);
  };

  const leaveEpisodeDetail = () => {
    if (
      editingPublicationMetadata &&
      publicationMetadataDirty &&
      !window.confirm(copy.episode.discardPublicationMetadataConfirm)
    ) {
      return;
    }
    onPublicationEditDirtyChange?.(false);
    onNavigate("episodes");
  };

  const prepareMetadataOnlyRevision = () => {
    if (!realEpisode) {
      return;
    }
    setPublicationError("");
    api
      .prepareMetadataOnlyRevision(realEpisode.id)
      .then(() => {
        setNotice(copy.episode.metadataRevisionPrepared);
        episode.reload();
      })
      .catch((reason: unknown) => {
        setPublicationError(
          reason instanceof Error ? reason.message : String(reason)
        );
      });
  };

  const savePublicationMetadata = () => {
    if (!realEpisode || chapterValidationMessage) {
      return;
    }
    setPublicationError("");
    setSavingMetadata(true);
    api
      .updateEpisodeMetadata(realEpisode.id, {
        title: realEpisode.title,
        overview: realEpisode.overview,
        publication_description: publicationDraft.description,
        chapters: publicationDraft.chapters.map((chapter, index) => ({
          title: chapter.title,
          start_seconds: parsedChapterTimes[index] ?? 0
        }))
      })
      .then((result) => {
        setNotice(
          result.changed === false
            ? copy.episode.metadataUnchanged
            : copy.episode.metadataSaved
        );
        setPublicationError("");
        setEditingPublicationMetadata(false);
        onPublicationEditDirtyChange?.(false);
        if (result.changed !== false) {
          episode.reload();
        }
      })
      .catch((reason: unknown) => {
        setPublicationError(
          reason instanceof Error ? reason.message : String(reason)
        );
      })
      .finally(() => setSavingMetadata(false));
  };

  const listenToSource = (seconds: number) => {
    if (!realEpisode?.source_url) {
      return;
    }
    try {
      const url = new URL(realEpisode.source_url);
      url.searchParams.set("t", String(Math.max(0, Math.floor(seconds))));
      window.open(url.toString(), "_blank", "noopener");
    } catch {
      window.open(realEpisode.source_url, "_blank", "noopener");
    }
  };

  const listenToOutput = (seconds: number) => {
    if (!realEpisode) {
      return;
    }
    const previewUrl = `/api/v1/episodes/${realEpisode.id}/preview`;
    if (
      player.episodeId !== realEpisode.id ||
      player.sourceUrl !== previewUrl
    ) {
      player.load(
        realEpisode.id,
        realEpisode.title,
        realEpisode.duration_seconds,
        previewUrl
      );
    }
    player.seek(seconds);
  };

  const ruleProcessingSegment = (
    ordinal: number,
    action: "keep" | "overturn",
    speakerId?: number
  ) => {
    if (!realEpisode) {
      return;
    }
    api
      .ruleSegment(realEpisode.id, ordinal, action, speakerId)
      .then(() => {
        setNotice(
          action === "overturn"
            ? copy.episode.processingOverturnNotice
            : copy.episode.processingRuledNotice
        );
        processing.reload();
      })
      .catch(failureNotice);
  };

  const markProcessingIssue = (ordinal: number) => {
    if (!realEpisode) {
      return;
    }
    api
      .markSegmentIssue(realEpisode.id, ordinal)
      .then(() => setNotice(copy.episode.processingMarkedNotice))
      .catch(failureNotice);
  };

  const disputedCount = processingDetail?.summary.disputed_count ?? 0;
  const processingSegments = processingDetail?.segments ?? [];
  const speakerNameByLabel = new Map<string, string>();
  processingSegments.forEach((segment) => {
    if (segment.speaker) {
      speakerNameByLabel.set(
        segment.speaker_label,
        segment.speaker.display_name
      );
    }
  });
  const visibleProcessingSegments =
    processingFilter === "all"
      ? processingSegments
      : processingSegments.filter(
          (segment) => segment.status === processingFilter
        );

  if (!episodeId) {
    return (
      <Card className="empty-state" role="alert">
        <CircleAlert size={20} aria-hidden="true" />
        <strong>{copy.episode.missingEpisodeTitle}</strong>
        <span>{copy.episode.missingEpisodeBody}</span>
        <Button size="sm" onClick={() => onNavigate("episodes")}>
          {copy.common.back}
        </Button>
      </Card>
    );
  }

  if (episode.status === "loading") {
    return (
      <AsyncResourceState
        kind="loading"
        surface="card"
        className="empty-state"
        icon={<RotateCcw size={20} aria-hidden="true" />}
        title={copy.episode.loadingTitle}
        detail={copy.episode.loadingBody}
      />
    );
  }

  if (episode.status === "error") {
    return (
      <AsyncResourceState
        kind="error"
        surface="card"
        className="empty-state"
        icon={<CircleAlert size={20} aria-hidden="true" />}
        title={copy.episode.loadErrorTitle}
        detail={episode.error}
        onRetry={episode.reload}
        retryLabel={copy.common.retry}
      />
    );
  }

  return (
    <>
      <div className="detail-status">
        <button type="button" onClick={leaveEpisodeDetail}>
          <ArrowLeft size={14} aria-hidden="true" />
          {copy.common.back}
        </button>
      </div>

      <div className="episode-detail-layout">
        <Card className="listener-preview">
          <div className="listener-mobile-header">
            <span>{copy.episode.listenerView}</span>
            <small>{copy.episode.listenerPlatform}</small>
          </div>

          <article className="listener-mobile-content">
            <img
              className="listener-episode-artwork"
              src={
                listenerPublishedMetadata?.artwork_url ??
                realEpisode?.artwork_url ??
                "/assets/transpodcast-artwork.png"
              }
              alt=""
              onError={(event) => {
                event.currentTarget.src =
                  "/assets/transpodcast-artwork.png";
              }}
            />
            <span className="listener-date">
              <CalendarDays size={13} aria-hidden="true" />
              <time dateTime={realEpisode?.source_published_at}>
                {episodeDate}
              </time>
            </span>
            <h1>{title}</h1>

            <button
              className="listener-show-row"
              type="button"
              onClick={() => {
                if (realEpisode?.source_url) {
                  window.open(realEpisode.source_url, "_blank", "noopener");
                }
              }}
            >
              <img src="/assets/transpodcast-artwork.png" alt="" />
              <span>
                <strong>{sourceShow}</strong>
                <small>{copy.episode.translatedEdition}</small>
              </span>
              <ChevronRight size={15} aria-hidden="true" />
            </button>

            <button
              className="listener-play-button"
              type="button"
              aria-label={
                player.playing && isLoadedEpisode
                  ? copy.common.pause
                  : copy.common.play
              }
              onClick={toggleEpisodePlayback}
            >
              {player.playing && isLoadedEpisode ? (
                <Pause size={20} fill="currentColor" aria-hidden="true" />
              ) : (
                <Play size={20} fill="currentColor" aria-hidden="true" />
              )}
              <strong>{duration}</strong>
            </button>
          </article>

          <div className="listener-notes">
            <section>
              <h2>
                <FileText size={16} aria-hidden="true" />
                {copy.episode.episodeNotes}
              </h2>
              <p>
                <strong>{copy.episode.originalEpisodeLabel}</strong>
                {sourceTitle}
              </p>
              <p className="listener-source-date">
                {copy.episode.sourceUpdatedLabel} {episodeDate}
              </p>
              <p className="listener-publication-description">
                {publicationDescription}
              </p>
            </section>

            {guestNames.length ? (
              <section>
                <h2>
                  <UserRound size={16} aria-hidden="true" />
                  {copy.episode.guestsTitle}
                </h2>
                <p>{guestNames.join(copy.episode.guestSeparator)}</p>
              </section>
            ) : null}

            {meaningfulChapters.length ? (
              <section>
                <h2>
                  <Clock3 size={16} aria-hidden="true" />
                  {copy.episode.timelineTitle}
                </h2>
                <div className="listener-timeline">
                  {meaningfulChapters.map((chapter) => (
                    <p key={chapter[0]}>
                      <time>{chapter[2]}</time>
                      <span>{chapter[0]}</span>
                    </p>
                  ))}
                </div>
              </section>
            ) : null}
          </div>
        </Card>

        <aside className="episode-review-panel">
          <Card className="review-workspace">
            <header className="review-workspace-header">
              <span className="review-card-icon">
                <ShieldCheck size={17} aria-hidden="true" />
              </span>
              <span className="review-workspace-title">
                <small>{copy.episode.ownerView}</small>
                <h2>{copy.episode.reviewTitle}</h2>
              </span>
              <div className="review-header-actions">
                <Badge
                  tone={
                    realEpisode?.state === "deleted"
                      ? "neutral"
                      : qualityReady
                        ? "success"
                        : "danger"
                  }
                >
                  {realEpisode?.state === "deleted"
                    ? recoveryExpired
                      ? copy.episode.recoveryExpired
                      : copy.episode.takenDown
                    : qualityReady
                      ? copy.episode.checksPassedShort
                      : copy.episode.needsReview}
                </Badge>
                {realEpisode?.state === "deleted" ? (
                  !recoveryExpired ? (
                    <Button
                      tone="secondary"
                      size="sm"
                      onClick={recoverEpisode}
                    >
                      <RotateCcw size={13} aria-hidden="true" />
                      {copy.episode.recover}
                    </Button>
                  ) : null
                ) : realEpisode?.published_metadata ? (
                  <Button
                    tone="danger"
                    size="sm"
                    onClick={takeDownEpisode}
                  >
                    <X size={13} aria-hidden="true" />
                    {copy.episode.takeDown}
                  </Button>
                ) : (
                  <Button
                    disabled={!qualityReady}
                    size="sm"
                    onClick={sendPublicationReview}
                  >
                    <Send size={13} aria-hidden="true" />
                    {copy.episode.sendPublish}
                  </Button>
                )}
              </div>
            </header>

            {!qualityReady ? (
              <div className="review-blocker">
                <CircleAlert size={17} aria-hidden="true" />
                <span>
                  <strong>{copy.episode.checksFailedTitle}</strong>
                  <small>{copy.episode.checksFailedBody}</small>
                </span>
              </div>
            ) : null}

            {episodeAudit ? (
              <div className="review-audit">
                <ListeningAuditPanel
                  audit={episodeAudit}
                  onListen={listenToOutput}
                />
              </div>
            ) : null}

            <div className="review-script-toolbar">
              <div
                className="segmented-tabs"
                role="tablist"
                aria-label={copy.episode.scriptReview}
              >
                <button
                  type="button"
                  role="tab"
                  className="segmented-tab"
                  data-active={reviewTab === "script" || undefined}
                  aria-selected={reviewTab === "script"}
                  onClick={() => selectReviewTab("script")}
                >
                  <FileText size={13} aria-hidden="true" />
                  {copy.episode.scriptTab}
                </button>
                <button
                  type="button"
                  role="tab"
                  className="segmented-tab"
                  data-active={reviewTab === "metadata" || undefined}
                  aria-selected={reviewTab === "metadata"}
                  onClick={() => selectReviewTab("metadata")}
                >
                  <ListMusic size={13} aria-hidden="true" />
                  {copy.episode.metadataTab}
                </button>
                <button
                  type="button"
                  role="tab"
                  className="segmented-tab"
                  data-active={reviewTab === "processing" || undefined}
                  aria-selected={reviewTab === "processing"}
                  onClick={() => selectReviewTab("processing")}
                >
                  {copy.episode.processingTab}
                  {disputedCount > 0 ? (
                    <span className="processing-count">
                      {disputedCount}
                    </span>
                  ) : null}
                </button>
              </div>
              <Button
                tone="ghost"
                size="sm"
                disabled={!realEpisode?.source_url}
                onClick={() => {
                  if (realEpisode?.source_url) {
                    window.open(realEpisode.source_url, "_blank", "noopener");
                  }
                }}
              >
                <ArrowUpRight size={13} aria-hidden="true" />
                {copy.episode.originalShort}
              </Button>
            </div>

            {reviewTab === "metadata" ? (
              <section className="review-pane publication-metadata-editor">
                <header className="review-pane-header">
                  <span>
                    <h3>{copy.episode.metadataTitle}</h3>
                    <p>{copy.episode.metadataHint}</p>
                  </span>
                  {!editingPublicationMetadata &&
                  publicationPresentation.state !== "revision_pending" ? (
                    <Button
                      tone="secondary"
                      size="sm"
                      disabled={editRefreshPhase !== "idle"}
                      onClick={beginPublicationMetadataEdit}
                    >
                      {editRefreshPhase === "idle"
                        ? copy.episode.editPublicationMetadata
                        : copy.episode.refreshingPublicationMetadata}
                    </Button>
                  ) : null}
                </header>
                {episode.error || publicationError ? (
                  <p className="publication-inline-error" role="alert">
                    {publicationError || episode.error}
                  </p>
                ) : null}
                {publicationPresentation.state === "revision_pending" ? (
                  <div className="publication-pending-panel">
                    <div className="publication-pending-state">
                      <div>
                        <strong>
                          {publicationPresentation.revision_kind === "metadata"
                            ? copy.episode.metadataPendingTitle
                            : copy.episode.audioRevisionPendingTitle}
                        </strong>
                        <small>
                          {publicationPresentation.revision_kind === "metadata"
                            ? copy.episode.metadataPendingHint
                            : copy.episode.audioRevisionPendingHint}
                        </small>
                      </div>
                      {publicationPresentation.revision_kind === "metadata" ? (
                        <div className="publication-pending-changes">
                          <small>{copy.episode.metadataChangedFields}</small>
                          <span>
                            {publicationPresentation.changed_fields
                              .map(copy.episode.metadataChangedField)
                              .join("、")}
                          </span>
                        </div>
                      ) : null}
                    </div>
                    {realEpisode?.published_metadata &&
                    publicationPresentation.revision_kind === "metadata" ? (
                      <details className="publication-change-details">
                        <summary>{copy.episode.metadataViewChanges}</summary>
                        {publicationPresentation.changed_fields
                          .filter((field) => field !== "artwork")
                          .map((field) => {
                            const currentValue = publicationFieldValue(
                              realEpisode,
                              field,
                              false
                            );
                            const pendingValue = publicationFieldValue(
                              realEpisode,
                              field,
                              true
                            );
                            return (
                              <article key={field}>
                                <strong>
                                  {copy.episode.metadataChangedField(field)}
                                </strong>
                                <div>
                                  <span>
                                    <small>{copy.episode.metadataCurrentValue}</small>
                                    <p>{currentValue}</p>
                                  </span>
                                  <span>
                                    <small>{copy.episode.metadataPendingValue}</small>
                                    <p>{pendingValue}</p>
                                  </span>
                                </div>
                              </article>
                            );
                          })}
                        {publicationPresentation.changed_fields.includes(
                          "artwork"
                        ) ? (
                          <article>
                            <strong>
                              {copy.episode.metadataChangedField("artwork")}
                            </strong>
                            <div className="publication-artwork-change">
                              <span>
                                <small>{copy.episode.metadataCurrentValue}</small>
                                <img
                                  src={realEpisode.published_metadata.artwork_url}
                                  alt=""
                                />
                              </span>
                              <span>
                                <small>{copy.episode.metadataPendingValue}</small>
                                <img src={realEpisode.artwork_url} alt="" />
                              </span>
                            </div>
                          </article>
                        ) : null}
                      </details>
                    ) : null}
                  </div>
                ) : null}
                {realEpisode &&
                publicationPresentation.backfill_available &&
                publicationPresentation.state === "published_clean" &&
                !editingPublicationMetadata ? (
                  <div className="publication-backfill-state">
                    <div>
                      <strong>{copy.episode.metadataBackfillTitle}</strong>
                      <small>{copy.episode.metadataBackfillHint}</small>
                      {realEpisode.publication_diagnostics.artwork_error ? (
                        <small>
                          {copy.episode.artworkErrorFor(
                            realEpisode.publication_diagnostics.artwork_error
                          )}
                        </small>
                      ) : null}
                      {realEpisode.publication_diagnostics.chapters
                        .excluded_reasons.length ? (
                        <small>
                          {realEpisode.publication_diagnostics.chapters.excluded_reasons
                            .map((diagnostic) =>
                              copy.episode.chapterDiagnosticReason(
                                diagnostic.reason,
                                diagnostic.count
                              )
                            )
                            .join("；")}
                        </small>
                      ) : null}
                    </div>
                    <Button
                      tone="secondary"
                      size="sm"
                      onClick={prepareMetadataOnlyRevision}
                    >
                      {copy.episode.metadataBackfillAction}
                    </Button>
                  </div>
                ) : null}
                {!editingPublicationMetadata ? (
                  <div className="publication-current-view">
                    <img
                      src={
                        realEpisode?.published_metadata?.artwork_url ??
                        realEpisode?.artwork_url
                      }
                      alt=""
                    />
                    <div>
                      <span>
                        {publicationPresentation.state === "draft"
                          ? copy.episode.draftVersion
                          : copy.episode.publishedVersion}
                      </span>
                      <p>
                        {realEpisode?.published_metadata?.description ??
                          publicationDraft.description}
                      </p>
                      <div className="publication-current-chapters">
                        {(realEpisode?.published_metadata?.chapters ??
                          realEpisode?.chapters ?? []).map((chapter) => (
                          <p key={`${chapter.start_seconds}-${chapter.title}`}>
                            <time>{formatClock(chapter.start_seconds)}</time>
                            <span>{chapter.title}</span>
                          </p>
                        ))}
                      </div>
                    </div>
                  </div>
                ) : (
                  <>
                <div
                  className="publication-artwork-status"
                  data-fallback={
                    realEpisode?.artwork.status !== "completed" || undefined
                  }
                >
                  <img
                    className="publication-artwork-preview"
                    src={
                      publicationPresentation.state === "published_clean"
                        ? (realEpisode?.published_metadata?.artwork_url ??
                          realEpisode?.artwork_url)
                        : realEpisode?.artwork_url
                    }
                    alt={copy.episode.artworkPreviewAlt}
                  />
                  <span>
                    {realEpisode?.artwork.status === "completed"
                      ? copy.episode.artworkReady
                      : copy.episode.artworkFallback}
                  </span>
                  <small>
                    {realEpisode?.artwork.status === "completed"
                      ? copy.episode.artworkReadyHint
                      : copy.episode.artworkFallbackHint}
                  </small>
                  {realEpisode?.publication_diagnostics.artwork_error ? (
                    <small>
                      {copy.episode.artworkErrorFor(
                        realEpisode.publication_diagnostics.artwork_error
                      )}
                    </small>
                  ) : null}
                </div>
                <label className="publication-description-field">
                  <span>{copy.episode.publicationDescriptionLabel}</span>
                  <textarea
                    value={publicationDraft.description}
                    disabled={!realEpisode}
                    onChange={(event) =>
                      setPublicationDraft((current) => ({
                        ...current,
                        description: event.target.value
                      }))
                    }
                  />
                </label>
                <div className="publication-chapter-editor">
                  <span>{copy.episode.chapterTitlesLabel}</span>
                  {publicationDraft.chapters.length ? (
                    publicationDraft.chapters.map((chapter, index) => (
                      <label key={index}>
                        <input
                          className="chapter-time-input"
                          type="text"
                          inputMode="numeric"
                          value={chapter.time}
                          aria-label={`${copy.episode.chapterTimeLabel} ${index + 1}`}
                          onChange={(event) => {
                            setPublicationDraft((current) => ({
                              ...current,
                              chapters: current.chapters.map(
                                (item, itemIndex) =>
                                  itemIndex === index
                                    ? { ...item, time: event.target.value }
                                    : item
                              )
                            }));
                          }}
                        />
                        <input
                          value={chapter.title}
                          maxLength={45}
                          aria-label={`${copy.episode.chapterTitleLabel} ${index + 1}`}
                          onChange={(event) => {
                            setPublicationDraft((current) => ({
                              ...current,
                              chapters: current.chapters.map(
                                (item, itemIndex) =>
                                  itemIndex === index
                                    ? { ...item, title: event.target.value }
                                    : item
                              )
                            }));
                          }}
                        />
                      </label>
                    ))
                  ) : (
                    <p>{copy.episode.noSemanticChapters}</p>
                  )}
                  {realEpisode?.publication_diagnostics.chapters
                    .excluded_reasons.length ? (
                    <div className="publication-chapter-diagnostics">
                      <small>{copy.episode.chapterDiagnosticsTitle}</small>
                      <ul>
                        {realEpisode.publication_diagnostics.chapters.excluded_reasons.map(
                          (diagnostic) => (
                            <li key={diagnostic.reason}>
                              {copy.episode.chapterDiagnosticReason(
                                diagnostic.reason,
                                diagnostic.count
                              )}
                            </li>
                          )
                        )}
                      </ul>
                    </div>
                  ) : null}
                  {chapterValidationMessage ? (
                    <p className="publication-field-error">
                      {chapterValidationMessage}
                    </p>
                  ) : null}
                </div>
                <div className="publication-metadata-actions">
                  <Button
                    tone="ghost"
                    onClick={resetPublicationMetadataEditor}
                  >
                    {copy.episode.cancelPublicationMetadata}
                  </Button>
                  <Button
                    disabled={
                      !realEpisode ||
                      savingMetadata ||
                      !publicationDraft.description.trim() ||
                      Boolean(chapterValidationMessage) ||
                      !publicationMetadataDirty
                    }
                    onClick={savePublicationMetadata}
                  >
                    {savingMetadata
                      ? copy.episode.metadataSaving
                      : copy.episode.metadataSave}
                  </Button>
                  <small>{copy.episode.metadataSaveHint}</small>
                </div>
                  </>
                )}
              </section>
            ) : reviewTab === "processing" ? (
              <section className="review-pane review-script-pane">
                <header className="review-pane-header">
                  <span>
                    <h3>{copy.episode.processingTitle}</h3>
                    <p>{copy.episode.processingHint}</p>
                  </span>
                </header>
                {processingDetail &&
                processingDetail.summary.evidence_available === false &&
                processingDetail.summary.historical_evidence_unavailable ? (
                  <p className="review-script-empty">
                    {copy.episode.processingEmpty}
                  </p>
                ) : processingDetail &&
                  processingDetail.summary.evidence_available === false ? (
                  <p className="review-script-empty">
                    {copy.episode.processingEvidenceUnavailable}
                  </p>
                ) : processingDetail && processingSegments.length ? (
                  <>
                    <div className="processing-summary">
                      <span>
                        <strong>
                          {processingDetail.summary.segment_count}
                        </strong>
                        {copy.episode.processingSummarySegments}
                      </span>
                      <span>
                        {copy.episode.processingSummaryDisputed}
                        <strong>
                          {processingDetail.summary.disputed_count}
                        </strong>
                      </span>
                      <span>
                        {copy.episode.processingSummaryCorrected}
                        <strong>
                          {processingDetail.summary.auto_corrected_count}
                        </strong>
                      </span>
                      {processingDetail.summary.speaker_tail_boundary_repair
                        ?.candidate_count ? (
                        <span>
                          {copy.episode.processingSummaryBoundaryRepairs}
                          <strong>
                            {
                              processingDetail.summary
                                .speaker_tail_boundary_repair.repair_count
                            }
                          </strong>
                          · {copy.episode.processingSummaryReviewed} {" "}
                          {
                            processingDetail.summary
                              .speaker_tail_boundary_repair.candidate_count
                          }
                        </span>
                      ) : null}
                      {processingDetail.summary.speaker_ownership_v15
                        ?.region_count ? (
                        <span>
                          {copy.episode.processingSummaryOwnershipRepairs}
                          <strong>
                            {
                              processingDetail.summary.speaker_ownership_v15
                                .changed_word_count
                            }
                          </strong>
                          · {copy.episode.processingSummaryReviewed} {" "}
                          {
                            processingDetail.summary.speaker_ownership_v15
                              .region_count
                          }
                        </span>
                      ) : null}
                      {processingDetail.summary.content_edit
                        .excluded_count > 0 ? (
                        <span>
                          {copy.episode.processingSummaryRemoved}
                          <strong>
                            {
                              processingDetail.summary.content_edit
                                .excluded_count
                            }
                          </strong>
                          · {processingDetail.summary.content_edit
                            .excluded_source_seconds.toFixed(1)}{" "}
                          {copy.episode.processingSummarySeconds}
                        </span>
                      ) : null}
                      {processingDetail.summary.listening_audit ? (
                        <span>
                          {copy.listeningAudit.summaryLabel}
                          <strong>
                            {copy.listeningAudit.counts(
                              processingDetail.summary.listening_audit
                                .hard_count,
                              processingDetail.summary.listening_audit
                                .soft_count,
                              processingDetail.summary.listening_audit
                                .unavailable_count
                            )}
                          </strong>
                        </span>
                      ) : null}
                      {processingDetail.summary.pipeline_version ? (
                        <span>
                          {processingDetail.summary.pipeline_version}
                        </span>
                      ) : null}
                      {processingDetail.summary.diarization_model ? (
                        <span>
                          {processingDetail.summary.diarization_model}
                          {processingDetail.summary
                            .diarization_model_revision
                            ? `@${processingDetail.summary.diarization_model_revision}`
                            : ""}
                        </span>
                      ) : null}
                    </div>
                    <div className="processing-filters">
                      {(
                        [
                          ["all", copy.episode.processingFilterAll],
                          [
                            "disputed",
                            copy.episode.processingFilterDisputed
                          ],
                          [
                            "auto_corrected",
                            copy.episode.processingFilterCorrected
                          ],
                          [
                            "archived",
                            copy.episode.processingFilterArchived
                          ]
                        ] as const
                      ).map(([value, label]) => (
                        <button
                          key={value}
                          type="button"
                          className="processing-chip"
                          data-active={
                            processingFilter === value || undefined
                          }
                          onClick={() => setProcessingFilter(value)}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    <div className="review-script-list">
                      {visibleProcessingSegments.map((segment) => {
                        // The overturn target comes from the
                        // adjudication record's actual counterpart;
                        // the first-other-speaker fallback only covers
                        // records without one (two-person episodes).
                        const counterpart =
                          segment.counterpart_speaker ??
                          (segment.adjudication?.kind ===
                          "speaker-ownership-adjudication"
                            ? null
                            : processingDetail.speakers.find(
                                (speaker) =>
                                  speaker.id !== segment.speaker?.id
                              )) ??
                          null;
                        return (
                          <article
                            key={segment.ordinal}
                            className={
                              segment.content_edit
                                ? "processing-content-edited"
                                : segment.status === "disputed"
                                ? "processing-flagged"
                                : undefined
                            }
                          >
                            <div className="review-segment-meta">
                              <strong>
                                {copy.episode.segmentPrefix}{" "}
                                {segment.ordinal}
                              </strong>
                              <small>
                                {formatClock(
                                  segment.source_start_seconds
                                )}{" "}
                                ·{" "}
                                {segment.speaker?.display_name ??
                                  formatSpeakerLabel(
                                    segment.speaker_label
                                  )}
                              </small>
                              {segment.status === "disputed" ? (
                                <Badge tone="warning">
                                  {copy.episode.processingFilterDisputed}
                                </Badge>
                              ) : null}
                              {segment.status === "auto_corrected" ? (
                                <Badge tone="primary">
                                  {copy.episode.processingFilterCorrected}
                                </Badge>
                              ) : null}
                              {segment.status === "archived" ? (
                                <Badge>
                                  {copy.episode.processingFilterArchived}
                                </Badge>
                              ) : null}
                              {segment.listening_audit_findings?.length ? (
                                <Badge
                                  tone={
                                    segment.listening_audit_findings.some(
                                      (finding) => finding.severity === "hard"
                                    )
                                      ? "danger"
                                      : "warning"
                                  }
                                >
                                  {copy.listeningAudit.title}
                                </Badge>
                              ) : null}
                              {segment.content_edit ? (
                                <Badge
                                  tone={
                                    segment.content_edit.state === "excluded"
                                      ? "warning"
                                      : "primary"
                                  }
                                >
                                  {segment.content_edit.state === "excluded"
                                    ? copy.episode.processingContentEditFull
                                    : copy.episode
                                        .processingContentEditPartial}
                                </Badge>
                              ) : null}
                            </div>
                            <div className="review-segment-copy">
                              <p>{segment.target_text ?? ""}</p>
                              <p className="processing-source">
                                {segment.source_text}
                              </p>
                              {segment.adjudication ? (
                                <p className="processing-evidence">
                                  {segment.adjudication.kind ===
                                  "speaker-ownership-adjudication" ? (
                                    <>
                                      {
                                        copy.episode
                                          .processingOwnershipPlanner
                                      }
                                      {": "}
                                      {segment.adjudication.evidence
                                        .planner_plan?.rationale ??
                                        copy.episode
                                          .processingDiscourseAbstained}
                                      {" ｜ "}
                                      {
                                        copy.episode
                                          .processingOwnershipReviewer
                                      }
                                      {": "}
                                      {segment.adjudication.evidence
                                        .reviewer_plan?.rationale ??
                                        copy.episode
                                          .processingDiscourseAbstained}
                                      {" ｜ "}
                                      {
                                        copy.episode
                                          .processingOwnershipApplied
                                      }
                                      {": "}
                                      {segment.adjudication.evidence
                                        .applied_plan_source ??
                                        copy.episode
                                          .processingOwnershipPreserved}
                                      {segment.adjudication.evidence
                                        .acoustic_conflict
                                        ? ` · ${copy.episode.processingOwnershipAcousticConflict}`
                                        : ""}
                                      {segment.adjudication.evidence
                                        .voiceprint_evidence?.length ? (
                                        <>
                                          {" ｜ "}
                                          {
                                            copy.episode
                                              .processingVoiceprint
                                          }
                                          {": "}
                                          {segment.adjudication.evidence.voiceprint_evidence
                                            .map(
                                              (item) =>
                                                `${speakerNameByLabel.get(item.owner) ?? formatSpeakerLabel(item.owner)} (${Object.entries(item.scores)
                                                  .map(
                                                    ([label, score]) =>
                                                      `${speakerNameByLabel.get(label) ?? formatSpeakerLabel(label)} ${Math.round(score * 100)}`
                                                  )
                                                  .join(" / ")})`
                                            )
                                            .join(" · ")}
                                        </>
                                      ) : null}
                                    </>
                                  ) : segment.adjudication.kind ===
                                  "speaker-tail-boundary-repair" ? (
                                    <>
                                      {segment.adjudication.verdict ===
                                      "tail-shifted" ? (
                                        <>
                                          {
                                            copy.episode
                                              .processingTailBoundaryRepair
                                          }
                                          {": "}
                                          {speakerNameByLabel.get(
                                            segment.adjudication.evidence
                                              .current_owner ?? ""
                                          ) ??
                                            formatSpeakerLabel(
                                              segment.adjudication.evidence
                                                .current_owner ?? ""
                                            )}
                                          {" → "}
                                          {speakerNameByLabel.get(
                                            segment.adjudication.evidence
                                              .next_owner ?? ""
                                          ) ??
                                            formatSpeakerLabel(
                                              segment.adjudication.evidence
                                                .next_owner ?? ""
                                            )}
                                          {" · "}
                                          {
                                            copy.episode
                                              .processingShiftStart
                                          }
                                          {": "}
                                          {segment.adjudication.evidence
                                            .shift_before_word_index ?? "—"}
                                        </>
                                      ) : (
                                        segment.adjudication.verdict ===
                                        "keep"
                                          ? copy.episode.processingTailRepairKept
                                          : copy.episode
                                              .processingTailRepairUnsure
                                      )}
                                      {" · "}
                                      {copy.episode.processingOriginalSpan}
                                      {": "}
                                      {segment.adjudication.subject
                                        ?.start_seconds != null &&
                                      segment.adjudication.subject
                                        ?.end_seconds != null
                                        ? `${formatClock(segment.adjudication.subject.start_seconds)}–${formatClock(segment.adjudication.subject.end_seconds)}`
                                        : "—"}
                                      {" · "}
                                      {segment.adjudication.rationale}
                                    </>
                                  ) : (
                                    <>
                                      {copy.episode.processingVoiceprint}
                                      {": "}
                                      {segment.adjudication.evidence
                                        .voiceprint_scores
                                        ? Object.entries(
                                            segment.adjudication.evidence
                                              .voiceprint_scores
                                          )
                                            .map(
                                              ([label, score]) =>
                                                `${speakerNameByLabel.get(label) ?? formatSpeakerLabel(label)} ${Math.round(score * 100)}`
                                            )
                                            .join(" · ")
                                        : "—"}
                                      {" ｜ "}
                                      {copy.episode.processingDiscourse}
                                      {": "}
                                      {segment.adjudication.evidence
                                        .discourse_rationale ??
                                        copy.episode
                                          .processingDiscourseAbstained}
                                    </>
                                  )}
                                </p>
                              ) : null}
                              {segment.content_edit ? (
                                <p className="processing-content-edit-reason">
                                  <strong>
                                    {segment.content_edit.category ===
                                    "paid_ad"
                                      ? copy.episode
                                          .processingContentEditPaidAd
                                      : copy.episode
                                          .processingContentEditOpeningHighlight}
                                  </strong>
                                  {" · "}
                                  {copy.episode.processingContentEditReason}
                                  {": "}
                                  {segment.content_edit.rationale}
                                  {" · "}
                                  {formatClock(
                                    segment.content_edit.source_start_seconds
                                  )}
                                  {"–"}
                                  {formatClock(
                                    segment.content_edit.source_end_seconds
                                  )}
                                  {segment.content_edit.evidence_segment_ids
                                    .length ? (
                                    <span className="processing-content-edit-evidence">
                                      {" · "}
                                      {
                                        copy.episode
                                          .processingContentEditEvidence
                                      }
                                      {": "}
                                      {segment.content_edit.evidence_segment_ids.map(
                                        (evidenceId, evidenceIndex) => {
                                          const evidenceSegment =
                                            processingSegments.find(
                                              (item) =>
                                                item.segment_id === evidenceId
                                            );
                                          return (
                                            <span key={evidenceId}>
                                              {evidenceIndex > 0 ? " · " : ""}
                                              <button
                                                type="button"
                                                disabled={!evidenceSegment}
                                                onClick={() => {
                                                  if (evidenceSegment) {
                                                    listenToSource(
                                                      evidenceSegment.source_start_seconds
                                                    );
                                                  }
                                                }}
                                              >
                                                {evidenceId}
                                              </button>
                                            </span>
                                          );
                                        }
                                      )}
                                    </span>
                                  ) : null}
                                </p>
                              ) : null}
                              {segment.listening_audit_findings?.length ? (
                                <ListeningAuditFindingList
                                  findings={segment.listening_audit_findings}
                                  onListen={listenToOutput}
                                />
                              ) : null}
                              <div className="processing-actions">
                                <Button
                                  tone="ghost"
                                  size="sm"
                                  disabled={!realEpisode?.source_url}
                                  onClick={() =>
                                    listenToSource(
                                      segment.source_start_seconds
                                    )
                                  }
                                >
                                  <Play size={12} aria-hidden="true" />
                                  {copy.episode.processingListenSource}
                                </Button>
                                <Button
                                  tone="ghost"
                                  size="sm"
                                  disabled={
                                    segment.output_start_seconds === null
                                  }
                                  onClick={() =>
                                    listenToOutput(
                                      segment.output_start_seconds ?? 0
                                    )
                                  }
                                >
                                  <ListMusic
                                    size={12}
                                    aria-hidden="true"
                                  />
                                  {copy.episode.processingListenOutput}
                                </Button>
                                {segment.status === "disputed" ? (
                                  <>
                                    <Button
                                      size="sm"
                                      onClick={() =>
                                        ruleProcessingSegment(
                                          segment.ordinal,
                                          "keep"
                                        )
                                      }
                                    >
                                      {copy.episode.processingKeep}
                                    </Button>
                                    {counterpart ? (
                                      <Button
                                        tone="secondary"
                                        size="sm"
                                        onClick={() =>
                                          ruleProcessingSegment(
                                            segment.ordinal,
                                            "overturn",
                                            counterpart.id
                                          )
                                        }
                                      >
                                        {
                                          copy.episode
                                            .processingOverturnPrefix
                                        }
                                        {counterpart.display_name}
                                      </Button>
                                    ) : null}
                                  </>
                                ) : (
                                  <Button
                                    tone="ghost"
                                    size="sm"
                                    onClick={() =>
                                      markProcessingIssue(segment.ordinal)
                                    }
                                  >
                                    {copy.episode.processingMarkIssue}
                                  </Button>
                                )}
                              </div>
                            </div>
                          </article>
                        );
                      })}
                      {!visibleProcessingSegments.length ? (
                        <p className="review-script-empty">
                          {processingEmptyLabel(processingFilter)}
                        </p>
                      ) : null}
                    </div>
                  </>
                ) : processing.status === "loading" ? (
                  <p className="review-script-empty" role="status">
                    {copy.episode.processingLoading}
                  </p>
                ) : processing.status === "error" ? (
                  <div className="review-script-empty" role="alert">
                    <p>{copy.episode.processingLoadError}</p>
                    <Button size="sm" onClick={processing.reload}>
                      {copy.common.retry}
                    </Button>
                  </div>
                ) : processingDetail ? (
                  <p className="review-script-empty">
                    {copy.episode.processingEmptyAll}
                  </p>
                ) : null}
              </section>
            ) : (
            <section className="review-pane review-script-pane">
              <header className="review-pane-header">
                <span>
                  <h3>{copy.episode.fullScript}</h3>
                  <p>
                    {transcriptSegments.length
                      ? `${copy.episode.scriptCountPrefix}${
                          transcriptSegments.length
                        }${copy.episode.scriptCountSuffix}`
                      : copy.episode.fullScriptHint}
                  </p>
                </span>
              </header>

              {transcriptSegments.length ? (
                <label className="review-script-search">
                  <Search size={15} aria-hidden="true" />
                  <span className="sr-only">
                    {copy.episode.searchReviewScript}
                  </span>
                  <input
                    type="search"
                    value={reviewSearch}
                    placeholder={copy.episode.searchReviewScript}
                    onChange={(event) => setReviewSearch(event.target.value)}
                  />
                </label>
              ) : null}

              <div className="review-script-list">
                {visibleReviewSegments.map((segment) => (
                  <article key={segment.ordinal}>
                    <div className="review-segment-meta">
                      <strong>
                        {copy.episode.segmentPrefix} {segment.ordinal}
                      </strong>
                      <small>
                        {formatSpeakerLabel(segment.speaker?.display_name)}
                      </small>
                    </div>
                    <div className="review-segment-copy">
                      <span>
                        <Languages size={13} aria-hidden="true" />
                        {copy.episode.translationLabel}
                      </span>
                      <p>{segment.target_text}</p>
                      <span>{copy.episode.sourceLabel}</span>
                      <p>{segment.source_text}</p>
                    </div>
                  </article>
                ))}
                {!transcriptSegments.length ? (
                  <p className="review-script-empty">{overview}</p>
                ) : null}
              </div>
            </section>
            )}

            <footer className="review-workspace-footer">
              <span>
                {qualityReady ? (
                  <CheckCircle2 size={13} aria-hidden="true" />
                ) : (
                  <CircleAlert size={13} aria-hidden="true" />
                )}
                {qualityReady
                  ? copy.episode.checksPassedTitle
                  : copy.episode.checksFailedTitle}
              </span>
              <small>{copy.episode.ownerControl}</small>
              {notice ? <p className="success-notice">{notice}</p> : null}
            </footer>
          </Card>
        </aside>
      </div>
    </>
  );
}

function episodeRowFromApi(
  item: ApiEpisodeLibraryItem,
  queueItem?: ApiProcessingQueueItem
): EpisodeRow {
  const libraryState = item.library_state;
  const actions = item.available_actions;
  const tone: EpisodeRow["statusTone"] =
    libraryState === "published"
      ? "success"
      : libraryState === "processing"
        ? "primary"
      : libraryState === "ready_to_publish"
        ? "primary"
        : libraryState === "action_required" ||
            libraryState === "ready_to_start"
          ? "warning"
          : "neutral";
  const bucket: EpisodeRow["bucket"] =
    libraryState === "published"
      ? "published"
      : libraryState === "processing"
        ? "processing"
        : libraryState === "ready_to_publish"
          ? "ready"
          : libraryState === "ready_to_start" ||
              libraryState === "queued" ||
              libraryState === "action_required"
            ? "pending"
            : libraryState === "taken_down"
              ? "taken_down"
              : "other";
  return {
    episodeId: item.episode_id,
    processingRunId: item.processing_run_id,
    show: item.source_show,
    title: item.title,
    overview:
      item.overview ??
      (libraryState === "ready_to_start"
        ? copy.queue.overview
        : copy.library.processingOverview),
    state: episodeLibraryStateLabel(libraryState),
    statusTone: tone,
    bucket,
    time: formatDuration(item.duration_seconds),
    context:
      item.episode_id === null
        ? queueItem
          ? queueOriginLabel(queueItem.origin)
          : copy.library.processingContext
        : copy.library.fromTransPodcast,
    libraryState,
    rawState: item.state,
    actions,
    youtubeVideoId: queueItem?.youtube_video_id,
    runStatus: queueItem?.status,
    artworkUrl: item.artwork_url
  };
}

function episodeRowKey(row: EpisodeRow): string {
  return row.episodeId !== null
    ? `episode-${row.episodeId}`
    : `run-${row.processingRunId ?? row.title}`;
}

function queueOriginLabel(
  origin: ApiProcessingQueueItem["origin"]
): string {
  if (origin === "manual") {
    return copy.queue.originManual;
  }
  if (origin === "backfill") {
    return copy.queue.originBackfill;
  }
  return copy.queue.originDiscovery;
}

function episodeLibraryStateLabel(
  state: EpisodeRow["libraryState"]
): string {
  const labels: Record<string, string> = {
    ready_to_start: copy.library.readyToStart,
    queued: copy.library.queued,
    processing: copy.library.processingActive,
    action_required: copy.library.actionRequired,
    ready_to_publish: copy.library.readyToPublish,
    published: copy.library.publishedState,
    unpublished: copy.library.unpublishedState,
    taken_down: copy.library.takenDownState
  };
  return labels[state] ?? copy.library.unknownState;
}

function processingEmptyLabel(
  filter: "all" | "disputed" | "auto_corrected" | "archived"
): string {
  if (filter === "disputed") {
    return copy.episode.processingEmptyDisputed;
  }
  if (filter === "auto_corrected") {
    return copy.episode.processingEmptyCorrected;
  }
  if (filter === "archived") {
    return copy.episode.processingEmptyArchived;
  }
  return copy.episode.processingEmptyAll;
}

function formatDuration(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

function formatListenerDuration(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours > 0
    ? `${hours} ${copy.episode.hourUnit} ${minutes} ${copy.episode.minuteUnit}`
    : `${minutes} ${copy.episode.minuteUnit}`;
}

function formatEpisodeDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric"
  }).format(date);
}

function isGenericChapterTitle(title: string): boolean {
  return /^(part|chapter|segment)\s*\d+$/i.test(title.trim());
}

function formatSpeakerLabel(displayName?: string | null): string {
  const match = displayName?.match(/^speaker_(\d+)/i);
  return match
    ? `${copy.episode.speakerPrefix} ${Number(match[1]) + 1}`
    : (displayName?.split(" · ")[0] ?? copy.episode.speakerUnknown);
}

function formatClock(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const remaining = safe % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`
    : `${minutes}:${String(remaining).padStart(2, "0")}`;
}

function parseClock(value: string): number | null {
  const parts = value.trim().split(":");
  if (
    ![2, 3].includes(parts.length) ||
    parts.some((part) => !/^\d+$/.test(part))
  ) {
    return null;
  }
  const numbers = parts.map(Number);
  const seconds = numbers.at(-1) ?? 0;
  const minutes = numbers.at(-2) ?? 0;
  const hours = numbers.length === 3 ? numbers[0] : 0;
  if (seconds >= 60 || minutes >= 60) {
    return null;
  }
  return hours * 3600 + minutes * 60 + seconds;
}

function publicationDraftForEpisode(
  episode: ApiEpisodeDetail
): PublicationMetadataDraft {
  return {
    description: episode.publication_description,
    chapters: episode.chapters.map((chapter) => ({
      time: formatClock(chapter.start_seconds),
      title: chapter.title
    }))
  };
}

function publicationFieldValue(
  episode: ApiEpisodeDetail,
  field: ApiPublicationPresentationField,
  pending: boolean
): string {
  const published = episode.published_metadata;
  if (field === "title") {
    return pending ? episode.title : (published?.title ?? episode.title);
  }
  if (field === "overview") {
    return pending ? episode.overview : (published?.overview ?? episode.overview);
  }
  if (field === "publication_description") {
    return pending
      ? episode.publication_description
      : (published?.description ?? episode.publication_description);
  }
  if (field === "chapters") {
    const chapters = pending
      ? episode.chapters
      : (published?.chapters ?? episode.chapters);
    return chapters
      .map((chapter) => `${formatClock(chapter.start_seconds)} ${chapter.title}`)
      .join("\n");
  }
  return "";
}

export function PageHeading({
  title,
  description,
  actions
}: {
  title: string;
  description: string;
  actions?: React.ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}
