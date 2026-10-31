import type { ApiEpisode, ApiLibraryItem, ApiRun } from "../lib/api";
import { EpisodeLibraryAction, EpisodeLibraryState } from "../lib/generated";

export function libraryItem(overrides: Partial<ApiLibraryItem>): ApiLibraryItem {
  return {
    artwork_url: "",
    available_actions: [EpisodeLibraryAction.VIEW],
    can_reprocess: false,
    created_at: "2026-10-30T10:00:00",
    duration_seconds: 3600,
    episode_id: null,
    guests: [],
    library_state: EpisodeLibraryState.PROCESSING,
    overview: null,
    processing_run_id: 1,
    published_at: null,
    source_published_at: "2026-10-29T10:00:00",
    source_show: "Synthetic Weekly",
    source_title: "Source title",
    source_url: "https://www.youtube.com/watch?v=source00001",
    stage: null,
    state: "processing",
    title: "Episode",
    ...overrides
  };
}

const STEP_IDS = [
  "source_archive",
  "source_analysis",
  "translation_authoring",
  "translation_review",
  "content_editing",
  "voice_render",
  "publication_audio_quality",
  "publication_queue"
];

export function run(
  overrides: Partial<ApiRun> & { stepStates?: string[] }
): ApiRun {
  const { stepStates, ...rest } = overrides;
  const states =
    stepStates ??
    STEP_IDS.map((_, index) => (index < 2 ? "completed" : index === 2 ? "active" : "not_started"));
  return {
    activity: [],
    attempt_count: 0,
    attempt_history: [],
    attempts: [],
    candidate_video_id: 1,
    completed_at: null,
    current_error: null,
    current_step: "translation_authoring",
    error: null,
    estimated_cost_usd: 1.25,
    first_claimed_at: "2026-10-31T10:00:00",
    id: 1,
    latest_change_at: "2026-10-31T10:00:00",
    owner_state: "processing",
    pipeline_steps: STEP_IDS.map((id, index) => ({
      completed_at: null,
      duration_ms: states[index] === "completed" ? 120000 : null,
      evidence_available: true,
      id,
      metrics: {},
      progress:
        id === "translation_authoring"
          ? { completed: 312, total: 598, unit: "targets" }
          : null,
      recovery: { checkpoint: true, retryable: true },
      started_at: null,
      state: states[index],
      summary: "",
      technical_evidence: null
    })),
    pipeline_version: "v23",
    priority: 10,
    queued_at: "2026-10-31T09:59:00",
    retry_plan: {
      invalidated_steps: [],
      preserves_translation_targets: true,
      resume_step: "publication_audio_quality",
      reused_steps: []
    },
    revision: "r1",
    source_show: "Synthetic Weekly",
    source_url: "https://www.youtube.com/watch?v=source00001",
    stage: "translation",
    stages: [],
    started_at: "2026-10-31T10:00:00",
    state: "processing",
    status: "processing",
    title: "Run title",
    translation: {
      input_tokens: 0,
      model: null,
      output_tokens: 0,
      provider: "claude-code"
    },
    translation_project: null,
    ...rest
  };
}

export function episode(overrides: Partial<ApiEpisode>): ApiEpisode {
  return {
    id: 8,
    processing_run_id: 8,
    title: "示例节目一",
    overview: "概要",
    publication_description: "第一段。\n\n第二段。",
    artwork_url: "/api/v1/episodes/8/artwork",
    state: "publication_queue",
    duration_seconds: 2400,
    source_show: "Example Show",
    source_title: "A fictional conversation about lighthouses",
    source_url: "https://www.youtube.com/watch?v=example0001",
    source_published_at: "2026-10-20T10:00:00",
    published_at: null,
    recoverable: false,
    chapters: [],
    published_metadata: null,
    quality_gates: {
      eligible: true,
      listening_audit: {
        status: "complete",
        policy_version: "listening-audit-v2",
        unit_count: 400,
        hard_count: 0,
        soft_count: 1,
        unavailable_count: 0,
        hard_unit_ids: [],
        blocking_unit_ids: [],
        owner_acknowledged: false,
        findings: [
          {
            unit_id: "seg-0012",
            parent_segment_id: "seg-0012",
            kind: "internal_silence",
            severity: "soft",
            detail: "1.4s silence",
            output_start_seconds: 40,
            output_end_seconds: 44
          }
        ],
        retry: null
      }
    },
    ...overrides
  };
}
