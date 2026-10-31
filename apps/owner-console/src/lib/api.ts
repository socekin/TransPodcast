import type {
  DirectSourceVideoImportResponse,
  EpisodeLibraryItemResponse,
  EpisodeLibraryResponse,
  ProcessingPipelineStepResponse,
  ProcessingRunDetailResponse,
  ProcessingRunDiagnosticsResponse,
  PublicationDecisionRequest,
  PublicationDecisionResponse,
  SettingsUpdate,
  SourceResolutionResponse
} from "./generated";

export type ApiLibraryItem = EpisodeLibraryItemResponse;
export type ApiRun = ProcessingRunDetailResponse;
export type ApiPipelineStep = ProcessingPipelineStepResponse;
export type ApiSourceResolution = SourceResolutionResponse;
export type ApiDirectImport = DirectSourceVideoImportResponse;

export type ApiChapter = {
  start_seconds: number;
  title: string;
};

export type ApiListeningAuditFinding = {
  unit_id: string;
  parent_segment_id: string | null;
  kind: string;
  severity: string;
  detail: string;
  output_start_seconds: number | null;
  output_end_seconds: number | null;
};

export type ApiListeningAudit = {
  status: string;
  policy_version: string | null;
  unit_count: number | null;
  hard_count: number;
  soft_count: number;
  unavailable_count: number;
  hard_unit_ids: string[];
  blocking_unit_ids: string[];
  owner_acknowledged: boolean;
  findings: ApiListeningAuditFinding[];
  retry: {
    status: string | null;
    unit_ids: string[] | null;
    resolved_unit_ids: string[] | null;
  } | null;
};

export function asListeningAudit(value: unknown): ApiListeningAudit | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const candidate = value as Partial<ApiListeningAudit>;
  return Array.isArray(candidate.findings) &&
    typeof candidate.status === "string"
    ? (candidate as ApiListeningAudit)
    : null;
}

export type ApiEpisode = {
  id: number;
  processing_run_id: number;
  title: string;
  overview: string;
  publication_description: string;
  artwork_url: string;
  state: string;
  duration_seconds: number;
  source_show: string;
  source_title: string;
  source_url: string;
  source_published_at: string;
  published_at: string | null;
  recoverable?: boolean;
  chapters: ApiChapter[];
  published_metadata: {
    title: string;
    overview: string;
    description: string;
    duration_seconds: number;
    chapters: ApiChapter[];
  } | null;
  quality_gates: Record<string, unknown>;
};

export type ApiTranscriptLine = {
  ordinal: number;
  segment_id: string;
  speaker_label: string;
  output_start_seconds: number | null;
  output_end_seconds: number | null;
  source_text: string;
  target_text: string;
};

export type AgentProvider = NonNullable<SettingsUpdate["agent_provider"]>;
export type SpeechModel = "indextts2" | "indextts25";

export type Readiness = {
  ready: boolean;
  services: Record<string, boolean>;
  details: Record<
    string,
    { configured: boolean; impact: string; optional?: boolean }
  >;
  agent?: {
    provider: AgentProvider;
    ready: boolean;
    providers: Partial<
      Record<
        AgentProvider,
        {
          configured: boolean;
          ready: boolean;
          cli_version: string;
          auth_method: string;
        }
      >
    >;
  };
};

export type ApiSettings = {
  speech: { model: SpeechModel };
  agent?: { provider: AgentProvider };
  publication: { subscription_url_masked: string };
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
      detail = ((await response.json()) as { detail?: string }).detail ?? "";
    } catch {
      detail = "";
    }
    throw new Error(
      detail || `TransPodcast API request failed with ${response.status}.`
    );
  }
  return response.json() as Promise<T>;
}

export function episodeAudioUrl(episodeId: number, published: boolean): string {
  return `/api/v1/episodes/${episodeId}/preview${published ? "?published=true" : ""}`;
}

export function renderedAudioUrl(runId: number): string {
  return `/api/v1/processing-runs/${runId}/rendered-audio`;
}

export const api = {
  library(signal?: AbortSignal) {
    return request<EpisodeLibraryResponse>(
      "/api/v1/episode-library",
      undefined,
      signal
    );
  },
  episode(episodeId: number, signal?: AbortSignal) {
    return request<ApiEpisode>(`/api/v1/episodes/${episodeId}`, undefined, signal);
  },
  run(runId: number, signal?: AbortSignal) {
    return request<ApiRun>(`/api/v1/processing-runs/${runId}`, undefined, signal);
  },
  transcript(runId: number, signal?: AbortSignal) {
    return request<{ processing_run_id: number; lines: ApiTranscriptLine[] }>(
      `/api/v1/processing-runs/${runId}/transcript`,
      undefined,
      signal
    );
  },
  diagnostics(runId: number) {
    return request<ProcessingRunDiagnosticsResponse>(
      `/api/v1/processing-runs/${runId}/diagnostics`
    );
  },
  retryRun(runId: number) {
    return request<ApiRun>(`/api/v1/processing-runs/${runId}:retry`, {
      method: "POST"
    });
  },
  closeRun(runId: number) {
    return request<ApiRun>(`/api/v1/processing-runs/${runId}:close`, {
      method: "POST"
    });
  },
  startRun(runId: number) {
    return request<{ id: number; state: string }>(
      `/api/v1/processing-runs/${runId}:start`,
      { method: "POST" }
    );
  },
  resolveSource(url: string, signal?: AbortSignal) {
    return request<ApiSourceResolution>(
      "/api/v1/source-resolutions",
      { method: "POST", body: JSON.stringify({ url }) },
      signal
    );
  },
  importResolvedVideo(resolutionId: string) {
    return request<ApiDirectImport>(
      `/api/v1/source-resolutions/${resolutionId}:import-video`,
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
      { method: "POST", body: JSON.stringify(body) }
    );
  },
  takeDown(episodeId: number) {
    return request<{ id: number; state: string }>(
      `/api/v1/episodes/${episodeId}`,
      { method: "DELETE" }
    );
  },
  recover(episodeId: number) {
    return request<{ id: number; state: string }>(
      `/api/v1/episodes/${episodeId}:recover`,
      { method: "POST" }
    );
  },
  readiness(signal?: AbortSignal) {
    return request<Readiness>("/api/v1/setup-readiness", undefined, signal);
  },
  settings(signal?: AbortSignal) {
    return request<ApiSettings>("/api/v1/settings", undefined, signal);
  },
  updateSettings(update: {
    speech_model?: SpeechModel;
    agent_provider?: AgentProvider;
  }) {
    return request<ApiSettings>("/api/v1/settings", {
      method: "PATCH",
      body: JSON.stringify(update)
    });
  },
  feedSubscription() {
    return request<{ subscription_url: string; apple_podcasts_url: string }>(
      "/api/v1/private-feed-subscription"
    );
  }
};
