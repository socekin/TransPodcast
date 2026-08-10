import type {
  ChannelSpeakerProfileResponse,
  ChannelSpeakerProfileUpdate,
  ChannelVoiceCandidateDecisionResponse,
  ChannelVoiceCandidateMerge,
  ChannelVoiceCandidateResponse,
  ChannelVoiceCandidateSave,
  ChannelVoiceLibraryDetailResponse,
  ChannelVoiceLibraryItemResponse,
  ChannelVoiceLibraryResponse,
  DirectSourceVideoImportResponse,
  EpisodeLibraryItemResponse,
  EpisodeLibraryResponse,
  PublicationDecisionRequest,
  PublicationDecisionResponse,
  ProcessingRunActivityResponse,
  ProcessingRunAttemptResponse,
  ProcessingRunDetailResponse,
  ProcessingRunDiagnosticsResponse,
  ProcessingRunSummaryResponse,
  ProcessingRunsResponse,
  SourceChannelResponse,
  SourceChannelResolutionResponse,
  SourceResolutionResponse,
  VoiceReferencePreviewResponse
} from "./generated";

export type ApiEpisodeLibraryItem = EpisodeLibraryItemResponse;

export type ApiEpisode = {
  id: number;
  title: string;
  overview: string;
  publication_description: string;
  artwork_url: string;
  artwork: Record<string, unknown>;
  publication_diagnostics: {
    artwork_error: string;
    chapters: {
      status: "ready" | "omitted";
      proposed_count: number;
      included_count: number;
      excluded_reasons: Array<{ reason: string; count: number }>;
    };
  };
  state: string;
  duration_seconds: number;
  source_show: string;
  source_title: string;
  source_url: string;
  source_published_at: string;
  guests: string[];
  created_at: string;
  published_at: string | null;
  recovery_until?: string | null;
  recoverable?: boolean;
};

export type ApiChapter = {
  start_seconds: number;
  title: string;
};

export type ApiPublicationPresentationField =
  | "title"
  | "overview"
  | "publication_description"
  | "chapters"
  | "artwork";

export type ApiProcessingSegment = {
  ordinal: number;
  segment_id: string;
  speaker_label: string;
  speaker: { id: number; display_name: string } | null;
  source_start_seconds: number;
  source_end_seconds: number;
  output_start_seconds: number | null;
  output_end_seconds: number | null;
  source_text: string;
  target_text: string | null;
  review_state: string;
  status: "accepted" | "disputed" | "auto_corrected" | "archived";
  owner_ruling: { action: string } | null;
  content_edit: {
    state: "excluded" | "partially_excluded";
    category: "paid_ad" | "opening_highlight";
    confidence: number;
    decision_id: string;
    source_start_seconds: number;
    source_end_seconds: number;
    rationale: string;
    evidence_segment_ids: string[];
  } | null;
  counterpart_speaker: {
    id: number;
    display_name: string;
    label: string;
  } | null;
  adjudication: {
    kind: string;
    verdict: string;
    rationale: string;
    subject?: {
      start_seconds?: number;
      end_seconds?: number;
    };
    evidence: {
      voiceprint_scores?: Record<string, number> | null;
      discourse_owner?: string | null;
      discourse_rationale?: string | null;
      shift_before_word_index?: number | null;
      current_owner?: string | null;
      next_owner?: string | null;
    };
  } | null;
};

export type ApiProcessingDetail = {
  episode_id: number;
  processing_run_id: number;
  summary: {
    evidence_available: boolean;
    historical_evidence_unavailable: boolean;
    pipeline_version: string | null;
    diarization_model: string | null;
    diarization_model_revision: string | null;
    segment_count: number;
    disputed_count: number;
    auto_corrected_count: number;
    speaker_tail_boundary_repair?: {
      policy_version: string | null;
      candidate_count: number;
      proposed_repair_count?: number;
      repair_count: number;
      locally_preserved_component_count?: number;
      locally_degraded_component_count?: number;
      conflict_component_count?: number;
      provider_degraded_component_count?: number;
      degraded: boolean;
    };
    speaker_mapping: Record<string, string>;
    consolidation_policy_version: string | null;
    adjudication_policy_version: string | null;
    content_edit: {
      status: "accepted" | "degraded" | "not_requested";
      excluded_count: number;
      excluded_source_seconds: number;
    };
  };
  speakers: Array<{ id: number; display_name: string }>;
  segments: ApiProcessingSegment[];
};

export type ApiEpisodeDetail = ApiEpisode & {
  preview: {
    key: string;
    available: boolean;
    expires_at: string;
  };
  chapters: ApiChapter[];
  published_metadata: {
    title: string;
    overview: string;
    duration_seconds: number;
    description: string;
    chapters: ApiChapter[];
    artwork_url: string;
    artwork: Record<string, unknown>;
  } | null;
  publication_presentation: {
    state: "draft" | "published_clean" | "revision_pending";
    revision_kind: "none" | "audio" | "metadata";
    changed_fields: ApiPublicationPresentationField[];
    backfill_available: boolean;
    degraded_fields: string[];
  };
  segments: Array<{
    ordinal: number;
    source_text: string;
    target_text: string;
    emotion_reference: string | null;
    version: number;
    speaker: {
      id: number;
      display_name: string;
      canonical_voice_reference: string;
    } | null;
  }>;
  apple_podcasts_preview: {
    artwork: string;
    audio_format: string;
    chapter_count: number;
    duration_seconds: number;
  };
  quality_gates: Record<string, unknown>;
};

export type Readiness = {
  ready: boolean;
  services: Record<string, boolean>;
  details: Record<
    string,
    {
      configured: boolean;
      impact: string;
      optional?: boolean;
    }
  >;
};

export type ApiChannel = {
  id: number;
  youtube_channel_id: string;
  title: string;
  url: string;
  mode: "agent_filtering" | "process_every_video" | "paused";
  avatar_url: string;
};

export type ApiChannelDetail = ApiChannel;

export type ApiSourceResolution = SourceResolutionResponse;
export type ApiDirectSourceVideoImport = DirectSourceVideoImportResponse;

export type ApiBackfillItem = {
  youtube_video_id: string;
  title: string;
  duration_seconds: number;
  source_url: string;
  published_at: string | null;
  view_count: number | null;
  is_requested: boolean;
};

export type ApiChannelRecentVideo = {
  youtube_video_id: string;
  title: string;
  duration_seconds: number;
  source_url: string;
  published_at: string | null;
  view_count: number | null;
  candidate_status: string | null;
  run_status: string | null;
  processing_run_id: number | null;
  episode_id: number | null;
};

export type ApiProcessingQueueItem = {
  processing_run_id: number;
  candidate_video_id: number;
  youtube_video_id: string;
  title: string;
  source_show: string;
  duration_seconds: number;
  status: string;
  stage: string;
  origin: "manual" | "backfill" | "discovery";
  created_at: string;
};

export type ApiCandidate = {
  id: number;
  youtube_video_id: string;
  title: string;
  description: string;
  duration_seconds: number;
  source_url: string;
  confidence: number;
  rationale: string;
  matched_interests: string;
  triage_decision?: string;
  title_zh?: string;
  summary_zh?: string;
  recommendation_zh?: string;
};

export type ApiIgnoredCandidate = {
  id: number;
  youtube_video_id: string;
  title: string;
  rationale: string;
};

export type ApiSpeaker = {
  id: number;
  display_name: string;
  canonical_voice_reference: string;
  verified: boolean;
  status: "verified" | "provisional";
};

export type ApiVoiceReference = VoiceReferencePreviewResponse;
export type ApiChannelVoiceSummary = ChannelVoiceLibraryItemResponse;
export type ApiChannelSpeakerProfile = ChannelSpeakerProfileResponse;
export type ApiChannelVoiceCandidate = ChannelVoiceCandidateResponse;
export type ApiChannelVoiceDetail = ChannelVoiceLibraryDetailResponse;
export type ApiRun = ProcessingRunDetailResponse;
export type ApiRunSummary = ProcessingRunSummaryResponse;
export type ApiRunAttempt = ProcessingRunAttemptResponse;
export type ApiRunActivity = ProcessingRunActivityResponse;
type ProcessingRunsResource = Omit<ProcessingRunsResponse, "revision"> & {
  revision?: ProcessingRunsResponse["revision"];
};

export type ApiSettings = {
  automation: {
    discovery_interval_minutes: number;
    duration_guardrail_minutes: number;
    processing_target_hours: number;
    manual_processing: boolean;
    auto_discovery: boolean;
  };
  retention: {
    intermediate_days: number;
    operational_log_days: number;
    operational_log_max_mb: number;
    recovery_window_days: number;
  };
  notifications: Record<string, boolean>;
  budget: {
    spend_guardrail_paused: boolean;
    authority: string;
  };
  secrets: Record<string, "configured" | "missing">;
  publication: {
    subscription_url_masked: string;
    token_replacement_requires_resubscription: boolean;
  };
};

type RequestErrorBody = {
  detail?: string;
};

async function request<T>(
  path: string,
  init?: RequestInit,
  signal?: AbortSignal
): Promise<T> {
  const response = await fetch(path, {
    ...init,
    signal,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers
    }
  });
  if (!response.ok) {
    let detail = "";
    try {
      detail = ((await response.json()) as RequestErrorBody).detail ?? "";
    } catch {
      detail = "";
    }
    throw new Error(
      detail || `TransPodcast API request failed with ${response.status}.`
    );
  }
  return response.json() as Promise<T>;
}

export const api = {
  episodeLibrary(signal?: AbortSignal) {
    return request<EpisodeLibraryResponse>(
      "/api/v1/episode-library",
      undefined,
      signal
    );
  },
  reprocessEpisode(episodeId: number) {
    return request<{
      episode_id: number;
      processing_run_id: number;
      state: string;
      stage: string;
      reused_checkpoint: string;
    }>(`/api/v1/episodes/${episodeId}:reprocess`, {
      method: "POST"
    });
  },
  prepareMetadataOnlyRevision(episodeId: number) {
    return request<{
      episode_id: number;
      state: string;
      revision_version: number;
    }>(
      `/api/v1/episodes/${episodeId}:prepare-metadata-only-revision`,
      { method: "POST" }
    );
  },
  updateEpisodeMetadata(
    episodeId: number,
    payload: {
      title: string;
      overview: string;
      publication_description: string;
      chapters: ApiChapter[];
    }
  ) {
    return request<{
      id: number;
      revision_version: number;
      state: string;
      changed: boolean;
    }>(`/api/v1/episodes/${episodeId}/metadata`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    });
  },
  episodes(signal?: AbortSignal) {
    return request<{ items: ApiEpisode[] }>("/api/v1/episodes", undefined, signal);
  },
  episode(episodeId: number, signal?: AbortSignal) {
    return request<ApiEpisodeDetail>(
      `/api/v1/episodes/${episodeId}`,
      undefined,
      signal
    );
  },
  processingDetail(episodeId: number, signal?: AbortSignal) {
    return request<ApiProcessingDetail>(
      `/api/v1/episodes/${episodeId}/processing-detail`,
      undefined,
      signal
    );
  },
  ruleSegment(
    episodeId: number,
    ordinal: number,
    action: "keep" | "overturn",
    speakerId?: number
  ) {
    return request<{
      episode_id: number;
      ordinal: number;
      action: string;
      review_state: string;
      revision_job_id: number | null;
    }>(`/api/v1/episodes/${episodeId}/segments/${ordinal}:rule`, {
      method: "POST",
      body: JSON.stringify({
        action,
        speaker_id: speakerId ?? null
      })
    });
  },
  markSegmentIssue(episodeId: number, ordinal: number, note = "") {
    return request<{
      episode_id: number;
      ordinal: number;
      artifact: { artifact_sha256: string };
    }>(`/api/v1/episodes/${episodeId}/segments/${ordinal}:mark-issue`, {
      method: "POST",
      body: JSON.stringify({ note })
    });
  },
  readiness(signal?: AbortSignal) {
    return request<Readiness>("/api/v1/setup-readiness", undefined, signal);
  },
  channels(signal?: AbortSignal) {
    return request<{ items: ApiChannel[] }>(
      "/api/v1/source-channels",
      undefined,
      signal
    );
  },
  resolveChannel(url: string) {
    return request<SourceChannelResolutionResponse>(
      "/api/v1/source-channels:resolve",
      {
        method: "POST",
        body: JSON.stringify({ url })
      }
    );
  },
  resolveSource(url: string, signal?: AbortSignal) {
    return request<ApiSourceResolution>(
      "/api/v1/source-resolutions",
      {
        method: "POST",
        body: JSON.stringify({ url })
      },
      signal
    );
  },
  resolutionBackfill(resolutionId: string) {
    return request<{
      resolution_id: string;
      channel_avatar_url: string;
      items: ApiBackfillItem[];
      timings_ms: Record<string, number>;
    }>(`/api/v1/source-resolutions/${resolutionId}:backfill`, {
      method: "POST"
    });
  },
  importResolvedVideo(resolutionId: string) {
    return request<ApiDirectSourceVideoImport>(
      `/api/v1/source-resolutions/${resolutionId}:import-video`,
      { method: "POST" }
    );
  },
  createChannel(
    url: string,
    mode: ApiChannel["mode"],
    backfillVideoIds: string[]
  ) {
    return request<ApiChannel>("/api/v1/source-channels", {
      method: "POST",
      body: JSON.stringify({
        url,
        mode,
        backfill_video_ids: backfillVideoIds
      })
    });
  },
  createChannelFromResolution(
    resolutionId: string,
    mode: ApiChannel["mode"],
    backfillVideoIds: string[]
  ) {
    return request<
      ApiChannelDetail & { imported_candidate_ids: number[] }
    >("/api/v1/source-channels", {
      method: "POST",
      body: JSON.stringify({
        resolution_id: resolutionId,
        mode,
        backfill_video_ids: backfillVideoIds
      })
    });
  },
  channelDetail(channelId: number, signal?: AbortSignal) {
    return request<SourceChannelResponse>(
      `/api/v1/source-channels/${channelId}`,
      undefined,
      signal
    );
  },
  updateChannelMode(channelId: number, mode: ApiChannel["mode"]) {
    return request<SourceChannelResponse>(
      `/api/v1/source-channels/${channelId}`,
      {
        method: "PATCH",
        body: JSON.stringify({ mode })
      }
    );
  },
  channelRecentVideos(
    channelId: number,
    refresh: boolean,
    signal?: AbortSignal
  ) {
    return request<{ items: ApiChannelRecentVideo[] }>(
      `/api/v1/source-channels/${channelId}/recent-videos${
        refresh ? "?refresh=true" : ""
      }`,
      undefined,
      signal
    );
  },
  importChannelVideo(channelId: number, youtubeVideoId: string) {
    return request<{
      id: number;
      youtube_video_id: string;
      state: string;
      run_status: string | null;
      processing_run_id: number | null;
    }>(`/api/v1/source-channels/${channelId}/candidate-videos`, {
      method: "POST",
      body: JSON.stringify({ youtube_video_id: youtubeVideoId })
    });
  },
  processingQueue(signal?: AbortSignal) {
    return request<{ items: ApiProcessingQueueItem[] }>(
      "/api/v1/processing-queue",
      undefined,
      signal
    );
  },
  startRun(runId: number) {
    return request<{ id: number; state: string; stage: string }>(
      `/api/v1/processing-runs/${runId}:start`,
      { method: "POST" }
    );
  },
  startAllRuns() {
    return request<{ started: number }>(
      "/api/v1/processing-runs:start-all",
      { method: "POST" }
    );
  },
  cancelRun(runId: number) {
    return request<{ id: number; state: string }>(
      `/api/v1/processing-runs/${runId}`,
      { method: "DELETE" }
    );
  },
  discover() {
    return request<Record<string, number>>("/api/v1/discovery-runs", {
      method: "POST"
    });
  },
  decisions(signal?: AbortSignal) {
    return request<{ items: ApiCandidate[] }>(
      "/api/v1/decision-queue",
      undefined,
      signal
    );
  },
  decide(candidateId: number, decision: "process" | "ignore") {
    return request<{ id: number; state: string }>(
      `/api/v1/candidate-videos/${candidateId}:decide`,
      { method: "POST", body: JSON.stringify({ decision }) }
    );
  },
  ignored(signal?: AbortSignal) {
    return request<{ items: ApiIgnoredCandidate[] }>(
      "/api/v1/ignored-list",
      undefined,
      signal
    );
  },
  processIgnored(candidateId: number) {
    return request<{ id: number; state: string }>(
      `/api/v1/candidate-videos/${candidateId}:process`,
      { method: "POST" }
    );
  },
  interest(signal?: AbortSignal) {
    return request<{ version: number; markdown: string; active: boolean }>(
      "/api/v1/interest-profile",
      undefined,
      signal
    );
  },
  updateInterest(markdown: string) {
    return request<{ version: number; markdown: string }>("/api/v1/interest-profile", {
      method: "PUT",
      body: JSON.stringify({ markdown })
    });
  },
  speakers(signal?: AbortSignal) {
    return request<{ items: ApiSpeaker[] }>("/api/v1/speakers", undefined, signal);
  },
  confirmSpeaker(speakerId: number) {
    return request<{ id: number; status: string; verified: boolean }>(
      `/api/v1/speakers/${speakerId}:confirm`,
      { method: "POST" }
    );
  },
  channelVoiceLibrary(signal?: AbortSignal) {
    return request<ChannelVoiceLibraryResponse>(
      "/api/v1/channel-voice-library",
      undefined,
      signal
    );
  },
  channelVoiceDetail(channelId: number, signal?: AbortSignal) {
    return request<ApiChannelVoiceDetail>(
      `/api/v1/source-channels/${channelId}/voice-library`,
      undefined,
      signal
    );
  },
  saveChannelVoiceCandidate(candidateId: number, displayName: string) {
    const payload: ChannelVoiceCandidateSave = {
      display_name: displayName.trim() || null
    };
    return request<ApiChannelSpeakerProfile>(
      `/api/v1/channel-voice-candidates/${candidateId}:save`,
      {
        method: "POST",
        body: JSON.stringify(payload)
      }
    );
  },
  ignoreChannelVoiceCandidate(candidateId: number) {
    return request<ChannelVoiceCandidateDecisionResponse>(
      `/api/v1/channel-voice-candidates/${candidateId}:ignore`,
      { method: "POST" }
    );
  },
  restoreChannelVoiceCandidate(candidateId: number) {
    return request<ChannelVoiceCandidateDecisionResponse>(
      `/api/v1/channel-voice-candidates/${candidateId}:restore`,
      { method: "POST" }
    );
  },
  mergeChannelVoiceCandidate(
    candidateId: number,
    targetProfileId: number,
    replaceCanonicalReference: boolean
  ) {
    const payload: ChannelVoiceCandidateMerge = {
      target_profile_id: targetProfileId,
      replace_canonical_reference: replaceCanonicalReference
    };
    return request<ApiChannelSpeakerProfile>(
      `/api/v1/channel-voice-candidates/${candidateId}:merge`,
      {
        method: "POST",
        body: JSON.stringify(payload)
      }
    );
  },
  stopChannelSpeakerProfile(profileId: number) {
    return request<ApiChannelSpeakerProfile>(
      `/api/v1/channel-speaker-profiles/${profileId}:stop-reuse`,
      { method: "POST" }
    );
  },
  renameChannelSpeakerProfile(profileId: number, displayName: string) {
    const payload: ChannelSpeakerProfileUpdate = {
      display_name: displayName
    };
    return request<ApiChannelSpeakerProfile>(
      `/api/v1/channel-speaker-profiles/${profileId}`,
      {
        method: "PATCH",
        body: JSON.stringify(payload)
      }
    );
  },
  runs(signal?: AbortSignal) {
    return request<ProcessingRunsResource>(
      "/api/v1/processing-runs",
      undefined,
      signal
    );
  },
  runDetail(runId: number, signal?: AbortSignal) {
    return request<ApiRun>(
      `/api/v1/processing-runs/${runId}`,
      undefined,
      signal
    );
  },
  retryRun(runId: number) {
    return request<ApiRun>(
      `/api/v1/processing-runs/${runId}:retry`,
      { method: "POST" }
    );
  },
  runDiagnostics(runId: number) {
    return request<ProcessingRunDiagnosticsResponse>(
      `/api/v1/processing-runs/${runId}/diagnostics`
    );
  },
  settings(signal?: AbortSignal) {
    return request<ApiSettings>("/api/v1/settings", undefined, signal);
  },
  updateSettings(update: Partial<ApiSettings["automation"]>) {
    return request<ApiSettings>("/api/v1/settings", {
      method: "PATCH",
      body: JSON.stringify(update)
    });
  },
  privateFeedSubscription() {
    return request<{
      subscription_url: string;
      apple_podcasts_url: string;
    }>("/api/v1/private-feed-subscription");
  },
  reviseSegment(
    episodeId: number,
    ordinal: number,
    update: {
      target_text: string;
      speaker_id?: number | null;
      emotion_reference?: string | null;
    }
  ) {
    return request<{
      episode_id: number;
      segment_ordinal: number;
      revision_job_id: number;
      revision_state: string;
    }>(`/api/v1/episodes/${episodeId}/segments/${ordinal}`, {
      method: "PATCH",
      body: JSON.stringify(update)
    });
  },
  reviseTranscript(
    episodeId: number,
    ordinal: number,
    sourceText: string
  ) {
    return request<{
      episode_id: number;
      segment_ordinal: number;
      revision_job_id: number;
      revision_state: string;
    }>(
      `/api/v1/episodes/${episodeId}/transcript-segments/${ordinal}`,
      {
        method: "PATCH",
        body: JSON.stringify({ source_text: sourceText })
      }
    );
  },
  notifyPublication(episodeId: number) {
    return request<{ id: number; state: string }>(
      `/api/v1/episodes/${episodeId}:notify-publication`,
      { method: "POST" }
    );
  },
  decidePublication(
    episodeId: number,
    decision: PublicationDecisionRequest["decision"],
    expectedState: string
  ) {
    const body: PublicationDecisionRequest = {
      decision,
      expected_state: expectedState
    };
    return request<PublicationDecisionResponse>(
      `/api/v1/episodes/${episodeId}:decide-publication`,
      {
      method: "POST",
        body: JSON.stringify(body)
      }
    );
  },
  takeDownEpisode(episodeId: number) {
    return request<{
      id: number;
      state: string;
      recoverable_for_days: number;
    }>(`/api/v1/episodes/${episodeId}`, { method: "DELETE" });
  },
  recoverEpisode(episodeId: number) {
    return request<{ id: number; state: string }>(
      `/api/v1/episodes/${episodeId}:recover`,
      { method: "POST" }
    );
  }
};
