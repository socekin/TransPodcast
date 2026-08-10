import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { runCopy } from "../i18n/runs";
import { formatDuration, Runs } from "./RunScreens";

const pipelineSteps = [
  "source_archive",
  "source_analysis",
  "translation_authoring",
  "translation_review",
  "voice_render",
  "publication_audio_quality",
  "publication_queue"
].map((id, index) => ({
  id,
  state: index < 2 ? "completed" : "not_started",
  started_at: index < 2 ? "2026-07-31T05:00:00Z" : null,
  completed_at: index < 2 ? "2026-07-31T05:00:01Z" : null,
  duration_ms: index < 2 ? 1200 : null,
  summary: index < 2 ? "Checkpoint completed" : "No recorded checkpoint",
  progress: null,
  metrics: {},
  evidence_available: index < 2,
  recovery: {
    retryable: false,
    checkpoint: index < 2
  },
  technical_evidence: null
}));

function runView(overrides: Record<string, unknown> = {}) {
  return {
    id: 8,
    revision: "run-revision-1",
    candidate_video_id: 18,
    title: "Building Reliable Code Agents",
    source_show: "Research Signals",
    source_url: "https://www.youtube.com/watch?v=example",
    status: "processing",
    state: "processing",
    stage: "translation",
    owner_state: "processing",
    current_step: "translation_authoring",
    latest_change_at: "2026-07-31T05:20:33Z",
    pipeline_version: "transpodcast-modal-pipeline-v7",
    estimated_cost_usd: 0.33,
    translation: {
      provider: "codex-cli",
      model: "codex-default",
      input_tokens: 1200,
      output_tokens: 300
    },
    current_error: null,
    retry_plan: {
      resume_step: "translation_authoring",
      reused_steps: ["source_archive", "source_analysis"],
      invalidated_steps: [
        "translation_authoring",
        "translation_review",
        "voice_render",
        "publication_audio_quality",
        "publication_queue"
      ],
      preserves_translation_targets: true
    },
    queued_at: "2026-07-31T04:58:00Z",
    first_claimed_at: "2026-07-31T05:00:00Z",
    started_at: "2026-07-31T05:00:00Z",
    completed_at: null,
    translation_project: {
      phase: "authoring",
      policy_version: "translation-policy-v3-project",
      completed_targets: 120,
      total_targets: 411,
      author_turns: 1,
      reviewer_turns: 0,
      author_sessions: ["author-session"],
      reviewer_sessions: []
    },
    pipeline_steps: pipelineSteps.map((step) =>
      step.id === "translation_authoring"
        ? {
            ...step,
            state: "active",
            progress: {
              completed: 120,
              total: 411,
              unit: "targets"
            }
          }
        : step
    ),
    stages: [],
    attempts: [],
    attempt_history: [],
    activity: [],
    ...overrides
  };
}

function runSummary(overrides: Record<string, unknown> = {}) {
  return {
    id: 8,
    revision: "run-revision-1",
    status: "processing",
    title: "Building Reliable Code Agents",
    source_show: "Research Signals",
    owner_state: "processing",
    current_step: "translation_authoring",
    estimated_cost_usd: 0.33,
    translation: {
      input_tokens: 1200,
      output_tokens: 300
    },
    latest_change_at: "2026-07-31T05:20:33Z",
    ...overrides
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Processing Run experience", () => {
  it("carries rounded seconds into the next minute", () => {
    expect(formatDuration(239_600)).toBe("4m 0s");
    expect(formatDuration(239_600)).not.toContain("60s");
  });
  it("keeps the current run visible while a background refresh is pending", async () => {
    let resolveRefresh:
      | ((response: Response) => void)
      | undefined;
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [runSummary()] }), {
          status: 200
        })
      )
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            resolveRefresh = resolve;
          })
      );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<Runs runId={null} onOpenRun={() => undefined} />);

    expect(
      await screen.findByText("Building Reliable Code Agents")
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: runCopy.actions.refresh })
    );

    expect(
      screen.getByText("Building Reliable Code Agents")
    ).toBeInTheDocument();
    expect(
      screen.queryByText(runCopy.states.initialLoading)
    ).not.toBeInTheDocument();

    resolveRefresh?.(
      new Response(
        JSON.stringify({
          items: [
            runSummary({ current_step: "translation_review" })
          ]
        }),
        { status: 200 }
      )
    );

    expect(
      await screen.findByText(runCopy.steps.translation_review)
    ).toBeInTheDocument();
  });

  it("does not replace visible data when the revision is unchanged", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            revision: "list-r1",
            items: [runSummary()]
          }),
          { status: 200 }
        )
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            revision: "list-r1",
            items: [
              runSummary({
                title: "This payload must not replace the current row"
              })
            ]
          }),
          { status: 200 }
        )
      );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<Runs runId={null} onOpenRun={() => undefined} />);

    expect(
      await screen.findByText("Building Reliable Code Agents")
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: runCopy.actions.refresh })
    );

    expect(
      screen.getByText("Building Reliable Code Agents")
    ).toBeInTheDocument();
    expect(
      screen.queryByText("This payload must not replace the current row")
    ).not.toBeInTheDocument();
  });

  it("keeps the last successful rows when refresh fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            revision: "list-r1",
            items: [runSummary()]
          }),
          { status: 200 }
        )
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "temporary outage" }), {
          status: 503
        })
      );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<Runs runId={null} onOpenRun={() => undefined} />);

    expect(
      await screen.findByText("Building Reliable Code Agents")
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: runCopy.actions.refresh })
    );

    expect(
      await screen.findByText(runCopy.states.stale)
    ).toBeInTheDocument();
    expect(
      screen.getByText("Building Reliable Code Agents")
    ).toBeInTheDocument();
  });

  it("opens a full run only after selecting its summary", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ revision: "list-r1", items: [runSummary()] }),
        { status: 200 }
      )
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const onOpenRun = vi.fn();

    render(<Runs runId={null} onOpenRun={onOpenRun} />);

    await user.click(
      await screen.findByRole("button", {
        name: `${runCopy.list.open}: Building Reliable Code Agents`
      })
    );

    expect(onOpenRun).toHaveBeenCalledWith(8);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/processing-runs",
      expect.anything()
    );
  });

  it("clears the current error after retry and retains it as history", async () => {
    const failedAttempt = {
      id: 41,
      sequence: 2,
      stage: "translation",
      status: "failed",
      duration_ms: 293144,
      transfer_bytes: 0,
      resume_checkpoint: "source_analysis",
      error_kind: "translation_schema",
      error_summary: "Translation worksheet target is unfinished",
      created_at: "2026-07-31T05:20:33Z",
      started_at: "2026-07-31T05:15:33Z",
      completed_at: "2026-07-31T05:20:33Z",
      correlation_id: "translation-attempt-41",
      routing: {},
      supersedes_attempt_id: null,
      timing_provenance: "legacy",
      is_current_blocker: true,
      superseded: false
    };
    const failed = runView({
      status: "review_queue",
      state: "review_queue",
      owner_state: "action_required",
      current_error: {
        kind: "translation_schema",
        summary: "Translation worksheet target is unfinished",
        step: "translation_authoring",
        retryable: true
      },
      attempt_history: [failedAttempt]
    });
    const retried = runView({
      status: "queued",
      state: "queued",
      owner_state: "queued",
      current_error: null,
      attempt_history: [
        {
          ...failedAttempt,
          is_current_blocker: false,
          superseded: true
        }
      ]
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (
          url === "/api/v1/processing-runs/8:retry"
          && init?.method === "POST"
        ) {
          return new Response(JSON.stringify(retried), { status: 200 });
        }
        if (url === "/api/v1/processing-runs/8") {
          return new Response(JSON.stringify(failed), { status: 200 });
        }
        throw new Error(`Unexpected request: ${url}`);
      })
    );
    const user = userEvent.setup();

    render(<Runs runId={8} onOpenRun={() => undefined} />);

    expect(
      await screen.findByText(
        runCopy.error.summaries.translation_schema
      )
    ).toBeInTheDocument();
    expect(screen.getByText(/进入队列/)).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: runCopy.actions.retry })
    );

    expect(
      screen.queryByRole("alert")
    ).not.toBeInTheDocument();
    expect(screen.getAllByText(runCopy.states.queued)).not.toHaveLength(0);
    expect(
      screen.queryByText(
        "Translation worksheet target is unfinished"
      )
    ).not.toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: runCopy.actions.history(1) })
    );
    const attemptSummary = screen.getByText("#2").closest("summary");
    expect(attemptSummary).not.toBeNull();
    expect(
      screen.getByText("Translation worksheet target is unfinished")
    ).not.toBeVisible();
    expect(screen.getByText(runCopy.states.superseded)).toBeInTheDocument();

    await user.click(attemptSummary!);
    expect(
      screen.getByText("Translation worksheet target is unfinished")
    ).toBeVisible();
    expect(screen.getByText(runCopy.history.legacyTiming)).toBeVisible();
  });

  it("shows Modal routing and parallel analysis evidence on demand", async () => {
    const routedSteps = pipelineSteps.map((step) =>
      step.id === "source_analysis"
        ? {
            ...step,
            technical_evidence: {
              version: "transpodcast-modal-pipeline-v10",
              provider: "modal",
              model: "Sortformer · Qwen3-ASR",
              modal_account: "transpodcast-secondary",
              modal_route: ["transpodcast-primary", "transpodcast-secondary"],
              parallel_operations: {
                diarization: 12.4,
                transcription: 18.6
              }
            }
          }
        : step
    );
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify(runView({ pipeline_steps: routedSteps })),
          { status: 200 }
        )
      )
    );
    const user = userEvent.setup();

    render(<Runs runId={8} onOpenRun={() => undefined} />);

    const heading = await screen.findByRole("heading", {
      name: runCopy.steps.source_analysis
    });
    const stage = heading.closest("article");
    expect(stage).not.toBeNull();
    await user.click(
      within(stage as HTMLElement).getByText(runCopy.actions.technical)
    );

    expect(
      within(stage as HTMLElement).getByText("transpodcast-secondary")
    ).toBeInTheDocument();
    expect(
      within(stage as HTMLElement).getByText(
        "transpodcast-primary → transpodcast-secondary"
      )
    ).toBeInTheDocument();
    expect(
      within(stage as HTMLElement).getByText(
        "diarization: 12.4s · transcription: 18.6s"
      )
    ).toBeInTheDocument();
  });

  it("shows queue timing and identifies local Translation work", async () => {
    const localSteps = pipelineSteps.map((step) =>
      step.id === "translation_authoring"
        ? {
            ...step,
            technical_evidence: {
              version: "translation-policy-v3-project",
              provider: "codex-cli",
              model: "codex-default",
              modal_account: null,
              modal_route: [],
              parallel_operations: null
            }
          }
        : step.id === "publication_audio_quality"
          ? {
              ...step,
              state: "active",
              technical_evidence: {
                version: null,
                provider: "local",
                model: null,
                modal_account: null,
                modal_route: [],
                parallel_operations: null
              }
            }
        : step
    );
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify(runView({ pipeline_steps: localSteps })),
          { status: 200 }
        )
      )
    );
    const user = userEvent.setup();

    render(<Runs runId={8} onOpenRun={() => undefined} />);

    expect(await screen.findByText(/进入队列/)).toBeInTheDocument();
    const heading = screen.getByRole("heading", {
      name: runCopy.steps.translation_authoring
    });
    const stage = heading.closest("article");
    expect(stage).not.toBeNull();
    await user.click(
      within(stage as HTMLElement).getByText(runCopy.actions.technical)
    );
    expect(
      within(stage as HTMLElement).getByText(runCopy.technical.localOnly)
    ).toBeInTheDocument();
    const qualityHeading = screen.getByRole("heading", {
      name: runCopy.steps.publication_audio_quality
    });
    const qualityStage = qualityHeading.closest("article");
    expect(qualityStage).not.toBeNull();
    await user.click(
      within(qualityStage as HTMLElement).getByText(
        runCopy.actions.technical
      )
    );
    expect(
      within(qualityStage as HTMLElement).getByText(
        runCopy.technical.localOnly
      )
    ).toBeInTheDocument();
  });

  it("copies a bounded diagnostics bundle from the API", async () => {
    const diagnostics = {
      schema_version: "processing-run-diagnostics-v1",
      run: { id: 8, state: "processing" },
      current_error: null,
      retry_plan: runView().retry_plan,
      translation: runView().translation,
      stages: [],
      attempts: []
    };
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          new Response(JSON.stringify(runView()), { status: 200 })
        )
        .mockResolvedValueOnce(
          new Response(JSON.stringify(diagnostics), { status: 200 })
        )
    );
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText }
    });

    render(<Runs runId={8} onOpenRun={() => undefined} />);
    const currentPanel = await screen.findByRole("complementary", {
      name: runCopy.currentPanel
    });
    expect(
      within(currentPanel).getByRole("button", {
        name: runCopy.actions.history(0)
      })
    ).toBeInTheDocument();
    await user.click(
      within(currentPanel).getByRole("button", {
        name: runCopy.actions.copyDiagnostics
      })
    );

    expect(
      await screen.findByRole("button", {
        name: runCopy.actions.diagnosticsCopied
      })
    ).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith(
      JSON.stringify(diagnostics, null, 2)
    );
  });
});
