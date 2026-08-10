import { Checkbox } from "@base-ui/react/checkbox";
import { Select } from "@base-ui/react/select";
import { Tooltip } from "@base-ui/react/tooltip";
import {
  Check,
  ChevronDown,
  Mic2,
  Pause,
  Pencil,
  Play,
  RotateCcw,
  Search,
  UserRound,
  X
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";

import {
  Badge,
  Button,
  Card,
  AsyncResourceState
} from "../components/ui";
import { copy } from "../i18n/zh-CN";
import {
  api,
  type ApiChannelSpeakerProfile,
  type ApiChannelVoiceCandidate,
  type ApiChannelVoiceDetail,
  type ApiChannelVoiceSummary,
  type ApiVoiceReference
} from "../lib/api";
import { useApiResource } from "../lib/useApiResource";
import { cn } from "../lib/utils";
import { PageHeading } from "./LibraryScreens";

type ActivePreview = {
  reference: ApiVoiceReference;
  label: string;
};

function initials(title: string): string {
  const words = title.trim().split(/\s+/).filter(Boolean);
  return words
    .slice(0, 2)
    .map((word) => word.slice(0, 1).toUpperCase())
    .join("");
}

function VoiceAvatar({
  title,
  className
}: {
  title: string;
  className?: string;
}) {
  return (
    <span className={cn("channel-voice-avatar", className)}>
      {initials(title)}
    </span>
  );
}

function formatReferenceDuration(reference: ApiVoiceReference): string {
  return reference.duration_seconds === null
    ? ""
    : `${reference.duration_seconds} ${copy.channelVoices.seconds}`;
}

function formatPlaybackTime(value: number): string {
  const safeValue = Number.isFinite(value) ? Math.max(0, value) : 0;
  const minutes = Math.floor(safeValue / 60);
  const seconds = Math.floor(safeValue % 60);
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

function firstActionError(
  errors: Record<string, string>,
  keys: string[]
): string {
  return keys.map((key) => errors[key]).find(Boolean) ?? "";
}

export function ChannelVoiceLibrary() {
  const [selectedChannelId, setSelectedChannelId] = useState<number | null>(
    null
  );
  const [search, setSearch] = useState("");
  const [names, setNames] = useState<Record<number, string>>({});
  const [pendingAction, setPendingAction] = useState("");
  const [notice, setNotice] = useState("");
  const [actionErrors, setActionErrors] = useState<Record<string, string>>({});
  const [undoCandidateId, setUndoCandidateId] = useState<number | null>(null);
  const [confirmingStopId, setConfirmingStopId] = useState<number | null>(null);
  const [editingProfileId, setEditingProfileId] = useState<number | null>(
    null
  );
  const [editingName, setEditingName] = useState("");
  const [mergingCandidateId, setMergingCandidateId] = useState<number | null>(
    null
  );
  const [mergeTargets, setMergeTargets] = useState<Record<number, number>>({});
  const [replaceReferences, setReplaceReferences] = useState<
    Record<number, boolean>
  >({});
  const [activePreview, setActivePreview] = useState<ActivePreview | null>(
    null
  );
  const [playingReferenceId, setPlayingReferenceId] = useState<number | null>(
    null
  );
  const [playbackTime, setPlaybackTime] = useState(0);
  const [playbackDuration, setPlaybackDuration] = useState(0);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const loadLibrary = useCallback(
    (signal: AbortSignal) => api.channelVoiceLibrary(signal),
    []
  );
  const library = useApiResource(loadLibrary);
  const summaries = library.data?.items ?? [];

  useEffect(() => {
    if (
      summaries.length > 0 &&
      (selectedChannelId === null ||
        !summaries.some(
          (item) => item.source_channel_id === selectedChannelId
        ))
    ) {
      setSelectedChannelId(summaries[0]?.source_channel_id ?? null);
    }
  }, [selectedChannelId, summaries]);

  const loadDetail = useCallback(
    (signal: AbortSignal): Promise<ApiChannelVoiceDetail | null> =>
      selectedChannelId === null
        ? Promise.resolve(null)
        : api.channelVoiceDetail(selectedChannelId, signal),
    [selectedChannelId]
  );
  const detail = useApiResource<ApiChannelVoiceDetail | null>(loadDetail);

  const filteredChannels = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return query
      ? summaries.filter((item) =>
          item.title.toLocaleLowerCase().includes(query)
        )
      : summaries;
  }, [search, summaries]);
  const totals = useMemo(
    () =>
      summaries.reduce(
        (current, item) => ({
          saved: current.saved + item.saved_profile_count,
          pending: current.pending + item.pending_candidate_count
        }),
        { saved: 0, pending: 0 }
      ),
    [summaries]
  );

  const refresh = useCallback(() => {
    library.reload();
    detail.reload();
  }, [detail, library]);

  const runAction = useCallback(
    async (
      key: string,
      operation: () => Promise<unknown>,
      message: string
    ): Promise<boolean> => {
      setPendingAction(key);
      setNotice("");
      setActionErrors((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
      try {
        await operation();
        setNotice(message);
        refresh();
        return true;
      } catch {
        setActionErrors((current) => ({
          ...current,
          [key]: copy.channelVoices.operationFailed
        }));
        return false;
      } finally {
        setPendingAction("");
      }
    },
    [refresh]
  );

  useEffect(() => {
    if (activePreview === null || audioRef.current === null) {
      return;
    }
    setPlaybackTime(0);
    setPlaybackDuration(activePreview.reference.duration_seconds ?? 0);
    audioRef.current.load();
    void audioRef.current.play().catch(() => {
      setPlayingReferenceId(null);
      setActionErrors((current) => ({
        ...current,
        [`preview-${activePreview.reference.id}`]:
          copy.channelVoices.playbackFailed
      }));
    });
  }, [activePreview]);

  const togglePreview = (reference: ApiVoiceReference, label: string) => {
    if (
      activePreview?.reference.id === reference.id &&
      audioRef.current !== null
    ) {
      if (audioRef.current.paused) {
        void audioRef.current.play().catch(() => {
          setActionErrors((current) => ({
            ...current,
            [`preview-${reference.id}`]: copy.channelVoices.playbackFailed
          }));
        });
      } else {
        audioRef.current.pause();
      }
      return;
    }
    setActionErrors((current) => {
      const next = { ...current };
      delete next[`preview-${reference.id}`];
      return next;
    });
    setActivePreview({ reference, label });
  };

  const closePreview = () => {
    audioRef.current?.pause();
    setActivePreview(null);
    setPlayingReferenceId(null);
    setPlaybackTime(0);
    setPlaybackDuration(0);
  };

  const saveCandidate = (candidate: ApiChannelVoiceCandidate) => {
    const key = `save-${candidate.id}`;
    void runAction(
      key,
      () =>
        api.saveChannelVoiceCandidate(
          candidate.id,
          names[candidate.id] ?? ""
        ),
      copy.channelVoices.savedNotice
    );
  };

  const ignoreCandidate = (candidate: ApiChannelVoiceCandidate) => {
    const key = `ignore-${candidate.id}`;
    void runAction(
      key,
      () => api.ignoreChannelVoiceCandidate(candidate.id),
      copy.channelVoices.ignoredNotice
    ).then((succeeded) => {
      if (succeeded) {
        setUndoCandidateId(candidate.id);
      }
    });
  };

  const restoreCandidate = () => {
    if (undoCandidateId === null) {
      return;
    }
    const candidateId = undoCandidateId;
    void runAction(
      `restore-${candidateId}`,
      () => api.restoreChannelVoiceCandidate(candidateId),
      ""
    ).then((succeeded) => {
      if (succeeded) {
        setUndoCandidateId(null);
      }
    });
  };

  const startMerge = (
    candidate: ApiChannelVoiceCandidate,
    savedProfiles: ApiChannelSpeakerProfile[]
  ) => {
    const firstProfile = savedProfiles[0];
    if (!firstProfile) {
      return;
    }
    setMergingCandidateId(candidate.id);
    setMergeTargets((current) => ({
      ...current,
      [candidate.id]: current[candidate.id] ?? firstProfile.id
    }));
  };

  const mergeCandidate = (candidate: ApiChannelVoiceCandidate) => {
    const targetProfileId = mergeTargets[candidate.id];
    if (!targetProfileId) {
      return;
    }
    void runAction(
      `merge-${candidate.id}`,
      () =>
        api.mergeChannelVoiceCandidate(
          candidate.id,
          targetProfileId,
          replaceReferences[candidate.id] ?? false
      ),
      copy.channelVoices.mergedNotice
    ).then((succeeded) => {
      if (succeeded) {
        setMergingCandidateId(null);
      }
    });
  };

  const stopReuse = (profile: ApiChannelSpeakerProfile) => {
    void runAction(
      `stop-${profile.id}`,
      () => api.stopChannelSpeakerProfile(profile.id),
      copy.channelVoices.stoppedNotice
    ).then((succeeded) => {
      if (succeeded) {
        setConfirmingStopId(null);
      }
    });
  };

  const startEditingName = (profile: ApiChannelSpeakerProfile) => {
    setEditingProfileId(profile.id);
    setEditingName(profile.display_name);
  };

  const saveName = (profile: ApiChannelSpeakerProfile) => {
    void runAction(
      `rename-${profile.id}`,
      () => api.renameChannelSpeakerProfile(profile.id, editingName.trim()),
      copy.channelVoices.nameSaved
    ).then((succeeded) => {
      if (succeeded) {
        setEditingProfileId(null);
      }
    });
  };

  const selectedDetail = detail.data;

  return (
    <Tooltip.Provider>
      <div className="channel-voice-page">
      <PageHeading
        title={copy.channelVoices.title}
        description={copy.channelVoices.description}
        actions={
          <div className="channel-voice-totals">
            <span>
              <strong>{totals.saved}</strong>
              {copy.channelVoices.savedTotal}
            </span>
            <span>
              <strong>{totals.pending}</strong>
              {copy.channelVoices.pendingTotal}
            </span>
          </div>
        }
      />

      {notice ? (
        <div className="channel-voice-notice" role="status">
          <span>{notice}</span>
          {undoCandidateId !== null ? (
            <Button
              size="sm"
              tone="ghost"
              onClick={restoreCandidate}
              disabled={pendingAction.startsWith("restore-")}
            >
              <RotateCcw size={13} aria-hidden="true" />
              {copy.channelVoices.undo}
            </Button>
          ) : null}
        </div>
      ) : null}

      {library.status === "loading" ? (
        <AsyncResourceState
          kind="loading"
          surface="card"
          className="channel-voice-loading"
          icon={<Mic2 size={18} aria-hidden="true" />}
          title={copy.channelVoices.loading}
        />
      ) : null}

      {library.status === "error" ? (
        <AsyncResourceState
          kind="error"
          surface="card"
          className="channel-voice-empty-page"
          icon={<Mic2 size={22} aria-hidden="true" />}
          title={copy.channelVoices.loadErrorTitle}
          detail={library.error}
          onRetry={library.reload}
          retryLabel={copy.common.retry}
        />
      ) : null}

      {library.status === "ready" && summaries.length === 0 ? (
        <Card className="channel-voice-empty-page">
          <Mic2 size={22} aria-hidden="true" />
          <strong>{copy.channelVoices.emptyChannelsTitle}</strong>
          <span>{copy.channelVoices.emptyChannelsBody}</span>
        </Card>
      ) : null}

      {summaries.length > 0 ? (
        <div className="channel-voice-layout">
          <Card className="channel-voice-channels">
            <header>
              <strong>{copy.channelVoices.byChannel}</strong>
              <span>{copy.channelVoices.byChannelHint}</span>
            </header>
            <label className="channel-voice-search">
              <Search size={14} aria-hidden="true" />
              <span className="sr-only">
                {copy.channelVoices.searchChannels}
              </span>
              <input
                type="search"
                value={search}
                placeholder={copy.channelVoices.searchChannels}
                onChange={(event) => setSearch(event.target.value)}
              />
            </label>
            <div className="channel-voice-channel-list">
              {filteredChannels.map((channel) => (
                <button
                  key={channel.source_channel_id}
                  className={cn(
                    "channel-voice-channel",
                    channel.source_channel_id === selectedChannelId &&
                      "is-active"
                  )}
                  type="button"
                  aria-current={
                    channel.source_channel_id === selectedChannelId
                      ? "true"
                      : undefined
                  }
                  onClick={() =>
                    setSelectedChannelId(channel.source_channel_id)
                  }
                >
                  <VoiceAvatar title={channel.title} />
                  <span className="channel-voice-channel-copy">
                    <strong>{channel.title}</strong>
                    <small>
                      {channel.saved_profile_count}{" "}
                      {copy.channelVoices.savedCount} ·{" "}
                      {channel.pending_candidate_count}{" "}
                      {copy.channelVoices.pendingCount}
                    </small>
                  </span>
                  {channel.pending_candidate_count > 0 ? (
                    <Badge tone="warning">
                      {channel.pending_candidate_count}
                    </Badge>
                  ) : (
                    <Check
                      className="channel-voice-channel-check"
                      size={15}
                      aria-hidden="true"
                    />
                  )}
                </button>
              ))}
            </div>
          </Card>

          {selectedDetail ? (
            <Card className="channel-voice-detail">
              <header className="channel-voice-channel-header">
                <VoiceAvatar
                  className="is-large"
                  title={selectedDetail.channel.title}
                />
                <span>
                  <h2>{selectedDetail.channel.title}</h2>
                  <p>{copy.channelVoices.followed}</p>
                </span>
                <span className="channel-voice-header-counts">
                  <Badge tone="success">
                    {selectedDetail.saved_profiles.length}{" "}
                    {copy.channelVoices.savedCount}
                  </Badge>
                  <Badge tone="warning">
                    {selectedDetail.pending_candidates.length}{" "}
                    {copy.channelVoices.pendingCount}
                  </Badge>
                </span>
              </header>

              <div className="channel-voice-rule">
                <Badge tone="primary">
                  {copy.channelVoices.reuseRuleLabel}
                </Badge>
                <span>{copy.channelVoices.reuseRule}</span>
              </div>

              {activePreview ? (
                <div className="channel-voice-player">
                  <Tooltip.Root>
                    <Tooltip.Trigger
                      className="channel-voice-player-toggle"
                      aria-label={
                        playingReferenceId === activePreview.reference.id
                          ? copy.channelVoices.pausePreview
                          : activePreview.label
                      }
                      onClick={() =>
                        togglePreview(
                          activePreview.reference,
                          activePreview.label
                        )
                      }
                    >
                      {playingReferenceId === activePreview.reference.id ? (
                        <Pause
                          size={14}
                          aria-hidden="true"
                          fill="currentColor"
                        />
                      ) : (
                        <Play
                          size={14}
                          aria-hidden="true"
                          fill="currentColor"
                        />
                      )}
                    </Tooltip.Trigger>
                    <VoiceTooltip>
                      {playingReferenceId === activePreview.reference.id
                        ? copy.channelVoices.pausePreview
                        : activePreview.label}
                    </VoiceTooltip>
                  </Tooltip.Root>
                  <span className="channel-voice-player-copy">
                    <strong>{copy.channelVoices.nowPlaying}</strong>
                    <small>{activePreview.label}</small>
                  </span>
                  <input
                    type="range"
                    aria-label={copy.channelVoices.playbackProgress}
                    min={0}
                    max={Math.max(playbackDuration, 0)}
                    step={0.1}
                    value={Math.min(playbackTime, playbackDuration)}
                    disabled={playbackDuration <= 0}
                    onChange={(event) => {
                      const nextTime = Number(event.target.value);
                      if (audioRef.current !== null) {
                        audioRef.current.currentTime = nextTime;
                      }
                      setPlaybackTime(nextTime);
                    }}
                  />
                  <span className="channel-voice-player-time">
                    {formatPlaybackTime(playbackTime)} /{" "}
                    {formatPlaybackTime(playbackDuration)}
                  </span>
                  <Tooltip.Root>
                    <Tooltip.Trigger
                      className="channel-voice-player-close"
                      aria-label={copy.channelVoices.closePreview}
                      onClick={closePreview}
                    >
                      <X size={14} aria-hidden="true" />
                    </Tooltip.Trigger>
                    <VoiceTooltip>
                      {copy.channelVoices.closePreview}
                    </VoiceTooltip>
                  </Tooltip.Root>
                  {actionErrors[
                    `preview-${activePreview.reference.id}`
                  ] ? (
                    <p className="channel-voice-item-error" role="alert">
                      {
                        actionErrors[
                          `preview-${activePreview.reference.id}`
                        ]
                      }
                    </p>
                  ) : null}
                </div>
              ) : null}

              <section className="channel-voice-section">
                <header>
                  <h3>{copy.channelVoices.savedTitle}</h3>
                  <p>{copy.channelVoices.savedHint}</p>
                </header>
                {selectedDetail.saved_profiles.length === 0 ? (
                  <div className="channel-voice-empty">
                    <span>
                      <strong>{copy.channelVoices.noSavedTitle}</strong>
                      <small>{copy.channelVoices.noSavedBody}</small>
                    </span>
                    <Badge tone="warning">
                      {copy.channelVoices.pendingCount}
                    </Badge>
                  </div>
                ) : (
                  <div className="channel-speaker-profiles">
                    {selectedDetail.saved_profiles.map((profile) => (
                      <article className="channel-speaker-profile" key={profile.id}>
                        <span className="channel-speaker-profile-avatar">
                          <UserRound size={17} aria-hidden="true" />
                        </span>
                        {editingProfileId === profile.id ? (
                          <span className="channel-speaker-profile-edit">
                            <input
                              aria-label={copy.channelVoices.editName}
                              value={editingName}
                              onChange={(event) =>
                                setEditingName(event.target.value)
                              }
                            />
                            <Button
                              size="sm"
                              onClick={() => saveName(profile)}
                              disabled={
                                !editingName.trim() ||
                                pendingAction === `rename-${profile.id}`
                              }
                            >
                              {copy.channelVoices.saveName}
                            </Button>
                            <Button
                              size="sm"
                              tone="ghost"
                              onClick={() => setEditingProfileId(null)}
                            >
                              {copy.channelVoices.cancelName}
                            </Button>
                          </span>
                        ) : (
                          <span className="channel-speaker-profile-name">
                            <strong>{profile.display_name}</strong>
                            <small>{copy.channelVoices.savedHint}</small>
                          </span>
                        )}
                        <VoicePreviewButton
                          reference={profile.voice_reference}
                          label={copy.channelVoices.listenCanonical}
                          playingReferenceId={playingReferenceId}
                          onToggle={togglePreview}
                        />
                        {editingProfileId !== profile.id ? (
                          <Button
                            size="sm"
                            tone="secondary"
                            onClick={() => startEditingName(profile)}
                          >
                            <Pencil size={12} aria-hidden="true" />
                            {copy.channelVoices.editName}
                          </Button>
                        ) : null}
                        <Button
                          size="sm"
                          tone="danger"
                          onClick={() => setConfirmingStopId(profile.id)}
                        >
                          {copy.channelVoices.stopReuse}
                        </Button>
                        {confirmingStopId === profile.id ? (
                          <div className="channel-stop-confirmation">
                            <p>{copy.channelVoices.stopReuseExplanation}</p>
                            <span>
                              <Button
                                size="sm"
                                tone="ghost"
                                onClick={() => setConfirmingStopId(null)}
                              >
                                {copy.channelVoices.cancelStopReuse}
                              </Button>
                              <Button
                                size="sm"
                                tone="danger"
                                disabled={
                                  pendingAction === `stop-${profile.id}`
                                }
                                onClick={() => stopReuse(profile)}
                              >
                                {copy.channelVoices.confirmStopReuse}
                              </Button>
                            </span>
                          </div>
                        ) : null}
                        {firstActionError(actionErrors, [
                          `rename-${profile.id}`,
                          `stop-${profile.id}`
                        ]) ? (
                          <p className="channel-voice-item-error" role="alert">
                            {firstActionError(actionErrors, [
                              `rename-${profile.id}`,
                              `stop-${profile.id}`
                            ])}
                          </p>
                        ) : null}
                      </article>
                    ))}
                  </div>
                )}
              </section>

              <section className="channel-voice-section is-pending">
                <header>
                  <h3>{copy.channelVoices.pendingTitle}</h3>
                  <p>{copy.channelVoices.pendingHint}</p>
                </header>
                {selectedDetail.pending_candidates.length === 0 ? (
                  <div className="channel-voice-empty">
                    <span>
                      <strong>{copy.channelVoices.noPendingTitle}</strong>
                      <small>{copy.channelVoices.noPendingBody}</small>
                    </span>
                    <Check size={18} aria-hidden="true" />
                  </div>
                ) : (
                  <div className="channel-voice-candidates">
                    {selectedDetail.pending_candidates.map(
                      (candidate, index) => (
                        <article
                          className="channel-voice-candidate"
                          key={candidate.id}
                        >
                          <span className="channel-voice-candidate-id">
                            <strong>
                              {copy.channelVoices.pendingVoice} {index + 1}
                            </strong>
                            <small>
                              {candidate.local_label} ·{" "}
                              {candidate.segment_count}{" "}
                              {copy.channelVoices.segments} ·{" "}
                              {copy.channelVoices.reference}{" "}
                              {formatReferenceDuration(
                                candidate.voice_reference
                              )}
                            </small>
                            <small className="channel-voice-source-episode">
                              {copy.channelVoices.sourceEpisode} ·{" "}
                              {candidate.source_episode.title}
                            </small>
                            {(candidate.possible_matches ?? []).map(
                              (match) => (
                                <small
                                  className="channel-voice-suggestion"
                                  key={match.profile_id}
                                >
                                  {copy.channelVoices.possibleMatch} ·{" "}
                                  {match.source_channel_title} ·{" "}
                                  {match.display_name} ·{" "}
                                  {Math.round(match.similarity * 100)}%
                                </small>
                              )
                            )}
                          </span>
                          <VoicePreviewButton
                            reference={candidate.voice_reference}
                            label={copy.channelVoices.listenCandidate}
                            playingReferenceId={playingReferenceId}
                            onToggle={togglePreview}
                          />
                          <input
                            className="channel-voice-name-input"
                            value={names[candidate.id] ?? ""}
                            placeholder={copy.channelVoices.namePlaceholder}
                            onChange={(event) =>
                              setNames((current) => ({
                                ...current,
                                [candidate.id]: event.target.value
                              }))
                            }
                          />
                          <span className="channel-voice-actions">
                            {selectedDetail.saved_profiles.length > 0 ? (
                              <Button
                                size="sm"
                                tone="ghost"
                                onClick={() =>
                                  startMerge(
                                    candidate,
                                    selectedDetail.saved_profiles
                                  )
                                }
                              >
                                {copy.channelVoices.mergeExisting}
                              </Button>
                            ) : null}
                            <Button
                              size="sm"
                              tone="secondary"
                              disabled={
                                pendingAction === `ignore-${candidate.id}`
                              }
                              onClick={() => ignoreCandidate(candidate)}
                            >
                              <X size={12} aria-hidden="true" />
                              {pendingAction === `ignore-${candidate.id}`
                                ? copy.channelVoices.ignoring
                                : copy.channelVoices.ignore}
                            </Button>
                            <Button
                              size="sm"
                              disabled={
                                pendingAction === `save-${candidate.id}`
                              }
                              onClick={() => saveCandidate(candidate)}
                            >
                              {pendingAction === `save-${candidate.id}`
                                ? copy.channelVoices.saving
                                : copy.channelVoices.saveReuse}
                            </Button>
                          </span>
                          {mergingCandidateId === candidate.id ? (
                            <div className="channel-voice-merge">
                              <label>
                                <span>{copy.channelVoices.mergeTarget}</span>
                                <Select.Root
                                  items={selectedDetail.saved_profiles.map(
                                    (profile) => ({
                                      label: profile.display_name,
                                      value: profile.id
                                    })
                                  )}
                                  value={mergeTargets[candidate.id] ?? null}
                                  onValueChange={(value) =>
                                    value !== null
                                      ? setMergeTargets((current) => ({
                                          ...current,
                                          [candidate.id]: value
                                        }))
                                      : undefined
                                  }
                                >
                                  <Select.Trigger className="channel-voice-select">
                                    <Select.Value />
                                    <Select.Icon>
                                      <ChevronDown
                                        size={13}
                                        aria-hidden="true"
                                      />
                                    </Select.Icon>
                                  </Select.Trigger>
                                  <Select.Portal>
                                    <Select.Positioner
                                      className="channel-voice-select-positioner"
                                      sideOffset={4}
                                    >
                                      <Select.Popup className="channel-voice-select-popup">
                                        <Select.List>
                                          {selectedDetail.saved_profiles.map(
                                            (profile) => (
                                              <Select.Item
                                                className="channel-voice-select-item"
                                                value={profile.id}
                                                key={profile.id}
                                              >
                                                <Select.ItemIndicator>
                                                  <Check
                                                    size={12}
                                                    aria-hidden="true"
                                                  />
                                                </Select.ItemIndicator>
                                                <Select.ItemText>
                                                  {profile.display_name}
                                                </Select.ItemText>
                                              </Select.Item>
                                            )
                                          )}
                                        </Select.List>
                                      </Select.Popup>
                                    </Select.Positioner>
                                  </Select.Portal>
                                </Select.Root>
                              </label>
                              <div className="channel-voice-replace">
                                <Checkbox.Root
                                  className="channel-voice-checkbox"
                                  aria-label={
                                    copy.channelVoices.replaceReference
                                  }
                                  checked={
                                    replaceReferences[candidate.id] ?? false
                                  }
                                  onCheckedChange={(checked) =>
                                    setReplaceReferences((current) => ({
                                      ...current,
                                      [candidate.id]: checked
                                    }))
                                  }
                                >
                                  <Checkbox.Indicator>
                                    <Check size={11} aria-hidden="true" />
                                  </Checkbox.Indicator>
                                </Checkbox.Root>
                                <span>
                                  <strong>
                                    {copy.channelVoices.replaceReference}
                                  </strong>
                                  <small>
                                    {copy.channelVoices.replaceReferenceHint}
                                  </small>
                                </span>
                              </div>
                              <span className="channel-voice-merge-actions">
                                <Button
                                  size="sm"
                                  tone="ghost"
                                  onClick={() => setMergingCandidateId(null)}
                                >
                                  {copy.channelVoices.cancelMerge}
                                </Button>
                                <Button
                                  size="sm"
                                  disabled={
                                    pendingAction === `merge-${candidate.id}`
                                  }
                                  onClick={() => mergeCandidate(candidate)}
                                >
                                  {copy.channelVoices.confirmMerge}
                                </Button>
                              </span>
                            </div>
                          ) : null}
                          {firstActionError(actionErrors, [
                            `save-${candidate.id}`,
                            `ignore-${candidate.id}`,
                            `merge-${candidate.id}`
                          ]) ? (
                            <p
                              className="channel-voice-item-error"
                              role="alert"
                            >
                              {firstActionError(actionErrors, [
                                `save-${candidate.id}`,
                                `ignore-${candidate.id}`,
                                `merge-${candidate.id}`
                              ])}
                            </p>
                          ) : null}
                        </article>
                      )
                    )}
                  </div>
                )}
              </section>
            </Card>
          ) : (
            <Card className="channel-voice-loading">
              <Mic2 size={18} aria-hidden="true" />
              {copy.channelVoices.loading}
            </Card>
          )}
        </div>
      ) : null}

      <audio
        ref={audioRef}
        src={activePreview?.reference.audio_url}
        preload="metadata"
        aria-label={activePreview?.label}
        onPlay={() =>
          setPlayingReferenceId(activePreview?.reference.id ?? null)
        }
        onPause={() => setPlayingReferenceId(null)}
        onEnded={() => setPlayingReferenceId(null)}
        onLoadedMetadata={(event) =>
          setPlaybackDuration(event.currentTarget.duration)
        }
        onDurationChange={(event) =>
          setPlaybackDuration(event.currentTarget.duration)
        }
        onTimeUpdate={(event) =>
          setPlaybackTime(event.currentTarget.currentTime)
        }
        onError={() => {
          if (activePreview !== null) {
            setActionErrors((current) => ({
              ...current,
              [`preview-${activePreview.reference.id}`]:
                copy.channelVoices.playbackFailed
            }));
          }
          setPlayingReferenceId(null);
        }}
      />
      </div>
    </Tooltip.Provider>
  );
}

function VoiceTooltip({ children }: { children: string }) {
  return (
    <Tooltip.Portal>
      <Tooltip.Positioner sideOffset={7}>
        <Tooltip.Popup className="channel-voice-tooltip">
          {children}
        </Tooltip.Popup>
      </Tooltip.Positioner>
    </Tooltip.Portal>
  );
}

function VoicePreviewButton({
  reference,
  label,
  playingReferenceId,
  onToggle
}: {
  reference: ApiVoiceReference;
  label: string;
  playingReferenceId: number | null;
  onToggle: (reference: ApiVoiceReference, label: string) => void;
}) {
  const playing = playingReferenceId === reference.id;
  return (
    <button
      className={cn("channel-voice-preview", playing && "is-playing")}
      type="button"
      aria-label={playing ? copy.channelVoices.pausePreview : label}
      onClick={() => onToggle(reference, label)}
    >
      {playing ? (
        <Pause size={14} aria-hidden="true" fill="currentColor" />
      ) : (
        <Play size={14} aria-hidden="true" fill="currentColor" />
      )}
      <span>
        {playing ? copy.channelVoices.pausePreview : label}
        {reference.duration_seconds !== null ? (
          <small>
            {reference.duration_seconds} {copy.channelVoices.seconds}
          </small>
        ) : null}
      </span>
    </button>
  );
}
