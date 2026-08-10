import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { copy } from "../i18n/zh-CN";
import { PlayerProvider } from "../lib/player";
import { EpisodeDetail } from "./LibraryScreens";

afterEach(() => {
  vi.unstubAllGlobals();
});

const EPISODE_ID = 7;

function episodePayload() {
  return {
    id: EPISODE_ID,
    title: "Robotics at the Edge · 测试节目",
    overview: "一段测试简介。",
    publication_description: "Expanded publication description.",
    artwork_url: `/api/v1/episodes/${EPISODE_ID}/artwork`,
    artwork: { status: "completed" },
    publication_diagnostics: {
      artwork_error: "",
      chapters: {
        status: "ready",
        proposed_count: 3,
        included_count: 3,
        excluded_reasons: []
      }
    },
    published_metadata: null,
    publication_presentation: {
      state: "draft",
      revision_kind: "none",
      changed_fields: [],
      backfill_available: false,
      degraded_fields: []
    },
    state: "preview_ready",
    duration_seconds: 3600,
    source_show: "Useful Show",
    source_title: "Robotics at the Edge",
    source_url: "https://www.youtube.com/watch?v=abc123",
    source_published_at: "2026-07-30T00:00:00Z",
    guests: [],
    created_at: "2026-07-31T00:00:00Z",
    published_at: null,
    preview: {
      key: "preview-key",
      available: true,
      expires_at: "2026-08-08T00:00:00Z"
    },
    chapters: [
      { start_seconds: 0, title: "Opening" },
      { start_seconds: 180, title: "Research" },
      { start_seconds: 360, title: "Outlook" }
    ],
    segments: [
      {
        ordinal: 1,
        segment_id: "seg-0001",
        source_text: "They did not feel compute bound",
        target_text: "他们当时不觉得算力是瓶颈。",
        emotion_reference: null,
        version: 1,
        speaker: {
          id: 11,
          display_name: "主持人",
          canonical_voice_reference: "ref-a"
        }
      }
    ],
    apple_podcasts_preview: {
      artwork: "artwork/transpodcast-3000.png",
      audio_format: "48 kHz stereo AAC-LC · 128 kbps",
      chapter_count: 0,
      duration_seconds: 3600
    },
    quality_gates: { eligible: true }
  };
}

function publishedEpisodePayload() {
  return {
    ...episodePayload(),
    state: "published",
    publication_description: "Currently published description.",
    publication_presentation: {
      state: "published_clean",
      revision_kind: "none",
      changed_fields: [],
      backfill_available: false,
      degraded_fields: []
    },
    published_at: "2026-08-01T00:00:00Z",
    published_metadata: {
      title: episodePayload().title,
      overview: episodePayload().overview,
      duration_seconds: episodePayload().duration_seconds,
      description: "Currently published description.",
      chapters: episodePayload().chapters,
      artwork_url: `/api/v1/episodes/${EPISODE_ID}/artwork?published=true`,
      artwork: { status: "completed", output_sha256: "published-hash" }
    }
  };
}

function pendingEpisodePayload() {
  return {
    ...publishedEpisodePayload(),
    state: "revision_pending",
    publication_description: "Pending revised description.",
    chapters: [
      { start_seconds: 0, title: "Revised opening" },
      { start_seconds: 240, title: "Revised research" },
      { start_seconds: 480, title: "Revised outlook" }
    ],
    publication_presentation: {
      state: "revision_pending",
      revision_kind: "metadata",
      changed_fields: ["publication_description", "chapters"],
      backfill_available: false,
      degraded_fields: []
    }
  };
}

function audioPendingEpisodePayload() {
  return {
    ...publishedEpisodePayload(),
    state: "revision_pending",
    publication_presentation: {
      state: "revision_pending",
      revision_kind: "audio",
      changed_fields: [],
      backfill_available: false,
      degraded_fields: []
    }
  };
}

function processingPayload(ruled: boolean) {
  return {
    episode_id: EPISODE_ID,
    processing_run_id: 8,
    summary: {
      evidence_available: true,
      historical_evidence_unavailable: false,
      pipeline_version: "transpodcast-modal-pipeline-v6",
      diarization_model: "fixture-sortformer",
      diarization_model_revision: "fixture-sortformer-revision",
      segment_count: 2,
      disputed_count: ruled ? 0 : 1,
      auto_corrected_count: 1,
      speaker_tail_boundary_repair: {
        policy_version: null as string | null,
        candidate_count: 0,
        repair_count: 0,
        degraded: false
      },
      speaker_mapping: {},
      consolidation_policy_version: "speaker-consolidation-v2",
      adjudication_policy_version: "boundary-ownership-adjudication-v1",
      content_edit: {
        status: "accepted",
        excluded_count: 1,
        excluded_source_seconds: 3.2
      }
    },
    speakers: [
      { id: 11, display_name: "主持人" },
      { id: 12, display_name: "Karpathy" }
    ],
    segments: [
      {
        ordinal: 1,
        speaker_label: "speaker-01",
        speaker: { id: 11, display_name: "主持人" },
        source_start_seconds: 0,
        source_end_seconds: 10,
        output_start_seconds: 0,
        output_end_seconds: 9,
        source_text: "They did not feel compute bound",
        target_text: "他们当时不觉得算力是瓶颈。",
        review_state: "accepted",
        status: "accepted",
        owner_ruling: null,
        content_edit: {
          state: "partially_excluded",
          category: "paid_ad",
          confidence: 0.98,
          decision_id: "paid-ad-1",
          source_start_seconds: 0,
          source_end_seconds: 3.2,
          rationale: "A separate third-party paid advertisement.",
          evidence_segment_ids: ["seg-0002"]
        },
        adjudication: null
      },
      {
        ordinal: 2,
        segment_id: "seg-0002",
        speaker_label: "speaker-02",
        speaker: { id: 12, display_name: "Karpathy" },
        source_start_seconds: 10,
        source_end_seconds: 10.5,
        output_start_seconds: 9,
        output_end_seconds: 9.6,
        source_text: "and",
        target_text: "而且",
        review_state: ruled ? "accepted" : "disputed",
        status: ruled ? "archived" : "disputed",
        owner_ruling: ruled ? { action: "keep" } : null,
        adjudication: {
          kind: "ownership-adjudication",
          verdict: "kept-discourse-abstained",
          rationale: "Only one vote remains.",
          subject: {
            start_seconds: 10,
            end_seconds: 10.5
          },
          evidence: {
            voiceprint_scores: {
              "speaker-01": 0.72,
              "speaker-02": 0.18
            },
            discourse_owner: null,
            discourse_rationale: null,
            shift_before_word_index: null as number | null,
            current_owner: null as string | null,
            next_owner: null as string | null
          }
        }
      }
    ]
  };
}

function stubApi() {
  let ruled = false;
  const calls: string[] = [];
  const fetchMock = vi.fn(
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.includes(":rule")) {
        ruled = true;
        return Promise.resolve(
          new Response(
            JSON.stringify({
              episode_id: EPISODE_ID,
              ordinal: 2,
              action: "keep",
              review_state: "accepted",
              revision_job_id: null
            }),
            { status: 200 }
          )
        );
      }
      if (url.includes("/processing-detail")) {
        return Promise.resolve(
          new Response(JSON.stringify(processingPayload(ruled)), {
            status: 200
          })
        );
      }
      if (url.includes(`/api/v1/episodes/${EPISODE_ID}`)) {
        return Promise.resolve(
          new Response(JSON.stringify(episodePayload()), {
            status: 200
          })
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ items: [] }), { status: 200 })
      );
    }
  );
  vi.stubGlobal("fetch", fetchMock);
  return { calls };
}

describe("Processing Detail tab", () => {
  it("updates publication copy and chapter metadata without audio work", async () => {
    const { calls } = stubApi();
    const user = userEvent.setup();
    render(
      <PlayerProvider>
        <EpisodeDetail
          onNavigate={() => undefined}
          episodeId={EPISODE_ID}
        />
      </PlayerProvider>
    );

    await user.click(
      await screen.findByRole("tab", {
        name: new RegExp(copy.episode.metadataTab)
      })
    );
    expect(screen.getByText(copy.episode.draftVersion)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: copy.episode.metadataBackfillAction })
    ).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "编辑发布信息" })
    );
    const description = await screen.findByLabelText(
      copy.episode.publicationDescriptionLabel
    );
    await user.clear(description);
    await user.type(description, "Revised publication description.");
    const firstTitle = await screen.findByLabelText(
      `${copy.episode.chapterTitleLabel} 1`
    );
    await user.clear(firstTitle);
    await user.type(firstTitle, "New opening");
    await user.click(
      screen.getByRole("button", { name: copy.episode.metadataSave })
    );

    expect(
      await screen.findByText(copy.episode.metadataSaved)
    ).toBeInTheDocument();
    expect(
      calls.some((call) =>
        call.includes(
          `PATCH /api/v1/episodes/${EPISODE_ID}/metadata`
        )
      )
    ).toBe(true);
    expect(
      calls.some((call) => call.includes("/speech"))
    ).toBe(false);
  });

  it("shows artwork and chapter validation diagnostics", async () => {
    const payload = {
      ...publishedEpisodePayload(),
      artwork: {
        status: "fallback",
        error: "Thumbnail response was HTML"
      },
      publication_diagnostics: {
        artwork_error: "Thumbnail response was HTML",
        chapters: {
          status: "ready",
          proposed_count: 5,
          included_count: 3,
          excluded_reasons: [
            { reason: "minimum_spacing", count: 2 }
          ]
        }
      },
      publication_presentation: {
        state: "published_clean",
        revision_kind: "none",
        changed_fields: [],
        backfill_available: true,
        degraded_fields: ["artwork", "publication_metadata"]
      }
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/processing-detail")) {
          return Promise.resolve(
            new Response(JSON.stringify(processingPayload(false)), {
              status: 200
            })
          );
        }
        return Promise.resolve(
          new Response(JSON.stringify(payload), { status: 200 })
        );
      })
    );
    const user = userEvent.setup();
    render(
      <PlayerProvider>
        <EpisodeDetail
          onNavigate={() => undefined}
          episodeId={EPISODE_ID}
        />
      </PlayerProvider>
    );

    await user.click(
      await screen.findByRole("tab", {
        name: new RegExp(copy.episode.metadataTab)
      })
    );

    expect(
      screen.getByText(
        copy.episode.artworkErrorFor("Thumbnail response was HTML")
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        copy.episode.chapterDiagnosticReason(
          "minimum_spacing",
          2
        )
      )
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "自动补全发布信息" })
    ).toBeInTheDocument();
  });

  it("shows every segment with evidence and rules a disputed one", async () => {
    const { calls } = stubApi();
    const user = userEvent.setup();
    render(
      <PlayerProvider>
        <EpisodeDetail
          onNavigate={() => undefined}
          episodeId={EPISODE_ID}
        />
      </PlayerProvider>
    );

    const processingTab = await screen.findByRole("tab", {
      name: new RegExp(copy.episode.processingTab)
    });
    await user.click(processingTab);

    expect(
      screen.getByRole("heading", {
        name: copy.episode.processingTitle
      })
    ).toBeInTheDocument();
    expect(screen.getByText("而且")).toBeInTheDocument();
    expect(
      screen.getByText(copy.episode.processingFilterDisputed, {
        selector: ".badge"
      })
    ).toBeInTheDocument();
    expect(
      screen.getByText(/主持人 72/)
    ).toBeInTheDocument();
    expect(
      screen.getByText(copy.episode.processingContentEditPartial, {
        selector: ".badge"
      })
    ).toBeInTheDocument();
    expect(
      document.querySelector(".review-pane-header .button")
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(
        new RegExp(copy.episode.processingContentEditPaidAd)
      )
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: copy.episode.processingKeep })
    );

    expect(
      await screen.findByText(copy.episode.processingRuledNotice)
    ).toBeInTheDocument();
    expect(
      calls.some((call) =>
        call.includes(`segments/2:rule`)
      )
    ).toBe(true);
    expect(
      await screen.findByText(copy.episode.processingFilterArchived, {
        selector: ".badge"
      })
    ).toBeInTheDocument();
  });

  it("uses truthful V14 empty copy for each processing filter", async () => {
    const detail = processingPayload(true);
    detail.summary.pipeline_version = "transpodcast-modal-pipeline-v14";
    detail.summary.auto_corrected_count = 0;
    detail.segments = detail.segments.map((segment) => ({
      ...segment,
      status: "accepted",
      review_state: "accepted"
    }));
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        return Promise.resolve(
          Response.json(
            url.includes("/processing-detail") ? detail : episodePayload()
          )
        );
      })
    );
    const user = userEvent.setup();

    render(
      <PlayerProvider>
        <EpisodeDetail
          onNavigate={() => undefined}
          episodeId={EPISODE_ID}
        />
      </PlayerProvider>
    );
    await user.click(
      await screen.findByRole("tab", {
        name: new RegExp(copy.episode.processingTab)
      })
    );

    expect(
      screen.getByRole("heading", { name: copy.episode.processingTitle })
    ).toBeInTheDocument();
    expect(screen.getByText("transpodcast-modal-pipeline-v14")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", {
        name: copy.episode.processingFilterDisputed
      })
    );
    expect(screen.getByText(copy.episode.processingEmptyDisputed)).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", {
        name: copy.episode.processingFilterCorrected
      })
    );
    expect(screen.getByText(copy.episode.processingEmptyCorrected)).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", {
        name: copy.episode.processingFilterArchived
      })
    );
    expect(screen.getByText(copy.episode.processingEmptyArchived)).toBeInTheDocument();
    expect(screen.queryByText(copy.episode.processingEmpty)).not.toBeInTheDocument();
  });

  it("reserves the historical limitation for explicitly unavailable evidence", async () => {
    const detail = processingPayload(false);
    detail.summary.evidence_available = false;
    detail.summary.historical_evidence_unavailable = true;
    detail.summary.segment_count = 0;
    detail.summary.disputed_count = 0;
    detail.summary.auto_corrected_count = 0;
    detail.segments = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        Response.json(
          String(input).includes("/processing-detail")
            ? detail
            : episodePayload()
        )
      )
    );
    const user = userEvent.setup();

    render(
      <PlayerProvider>
        <EpisodeDetail
          onNavigate={() => undefined}
          episodeId={EPISODE_ID}
        />
      </PlayerProvider>
    );
    await user.click(
      await screen.findByRole("tab", {
        name: new RegExp(copy.episode.processingTab)
      })
    );

    expect(screen.getByText(copy.episode.processingEmpty)).toBeInTheDocument();
    expect(
      screen.queryByText(copy.episode.processingEmptyAll)
    ).not.toBeInTheDocument();
  });

  it("does not describe missing V14 evidence as a V6 baseline", async () => {
    const detail = processingPayload(false);
    detail.summary.pipeline_version = "transpodcast-modal-pipeline-v14";
    detail.summary.evidence_available = false;
    detail.summary.historical_evidence_unavailable = false;
    detail.summary.segment_count = 0;
    detail.segments = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        Response.json(
          String(input).includes("/processing-detail")
            ? detail
            : episodePayload()
        )
      )
    );
    const user = userEvent.setup();

    render(
      <PlayerProvider>
        <EpisodeDetail
          onNavigate={() => undefined}
          episodeId={EPISODE_ID}
        />
      </PlayerProvider>
    );
    await user.click(
      await screen.findByRole("tab", {
        name: new RegExp(copy.episode.processingTab)
      })
    );

    expect(
      screen.getByText(copy.episode.processingEvidenceUnavailable)
    ).toBeInTheDocument();
    expect(
      screen.queryByText(copy.episode.processingEmpty)
    ).not.toBeInTheDocument();
  });

  it("shows a recorded adjacent Speaker boundary repair as an automatic correction", async () => {
    const detail = processingPayload(false);
    detail.summary.pipeline_version = "transpodcast-modal-pipeline-v12";
    detail.summary.diarization_model =
      "nvidia/diar_streaming_sortformer_4spk-v2.1";
    detail.summary.diarization_model_revision = "fafaab5faa16";
    detail.summary.disputed_count = 0;
    detail.summary.speaker_tail_boundary_repair = {
      policy_version: "speaker-tail-boundary-repair-v2",
      candidate_count: 18,
      repair_count: 7,
      degraded: false
    };
    const splitSegment = detail.segments[1];
    if (!splitSegment.adjudication) {
      throw new Error("Split fixture requires adjudication evidence");
    }
    splitSegment.review_state = "auto_corrected";
    splitSegment.status = "auto_corrected";
    splitSegment.adjudication.kind = "speaker-tail-boundary-repair";
    splitSegment.adjudication.verdict = "tail-shifted";
    splitSegment.adjudication.rationale = "A new answer begins with Yes.";
    splitSegment.adjudication.subject = {
      start_seconds: 346.864,
      end_seconds: 350.064
    };
    splitSegment.adjudication.evidence.shift_before_word_index = 2;
    splitSegment.adjudication.evidence.current_owner = "speaker-02";
    splitSegment.adjudication.evidence.next_owner = "speaker-01";
    const unsureSegment = JSON.parse(JSON.stringify(splitSegment));
    unsureSegment.ordinal = 3;
    unsureSegment.segment_id = "seg-0093";
    unsureSegment.source_text = "I see";
    unsureSegment.target_text = "我明白了";
    unsureSegment.status = "accepted";
    unsureSegment.review_state = "accepted";
    unsureSegment.adjudication.verdict = "unsure";
    unsureSegment.adjudication.rationale =
      "An overlapping acknowledgement is ambiguous.";
    detail.segments.push(unsureSegment);
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        return Promise.resolve(
          new Response(
            JSON.stringify(
              url.includes("/processing-detail")
                ? detail
                : episodePayload()
            ),
            { status: 200 }
          )
        );
      })
    );
    const user = userEvent.setup();
    render(
      <PlayerProvider>
        <EpisodeDetail
          onNavigate={() => undefined}
          episodeId={EPISODE_ID}
        />
      </PlayerProvider>
    );

    await user.click(
      await screen.findByRole("tab", {
        name: new RegExp(copy.episode.processingTab)
      })
    );

    expect(
      screen.getByText(/Karpathy → 主持人/)
    ).toHaveTextContent(copy.episode.processingOriginalSpan);
    expect(
      screen.getByText(copy.episode.processingFilterCorrected, {
        selector: ".badge"
      })
    ).toBeInTheDocument();
    expect(
      screen.getByText(new RegExp(copy.episode.processingSummaryReviewed))
    ).toHaveTextContent("18");
    expect(
      screen.getByText(new RegExp(copy.episode.processingSummaryReviewed))
    ).toHaveTextContent("7");
    expect(
      screen.getByText(
        "nvidia/diar_streaming_sortformer_4spk-v2.1@fafaab5faa16"
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText(copy.episode.processingTailRepairUnsure, {
        exact: false
      })
    ).toHaveTextContent("An overlapping acknowledgement is ambiguous.");
  });

  it("offers an overturn to the other Speaker on disputed segments", async () => {
    stubApi();
    const user = userEvent.setup();
    render(
      <PlayerProvider>
        <EpisodeDetail
          onNavigate={() => undefined}
          episodeId={EPISODE_ID}
        />
      </PlayerProvider>
    );

    await user.click(
      await screen.findByRole("tab", {
        name: new RegExp(copy.episode.processingTab)
      })
    );

    expect(
      screen.getByRole("button", {
        name: `${copy.episode.processingOverturnPrefix}主持人`
      })
    ).toBeInTheDocument();
  });
});

describe("Published episode lifecycle", () => {
  it("shows one current publication view when no revision is pending", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/processing-detail")) {
          return Promise.resolve(
            new Response(JSON.stringify(processingPayload(false)), {
              status: 200
            })
          );
        }
        return Promise.resolve(
          new Response(JSON.stringify(publishedEpisodePayload()), {
            status: 200
          })
        );
      })
    );
    const user = userEvent.setup();
    render(
      <PlayerProvider>
        <EpisodeDetail
          onNavigate={() => undefined}
          episodeId={EPISODE_ID}
        />
      </PlayerProvider>
    );

    await user.click(
      await screen.findByRole("tab", {
        name: new RegExp(copy.episode.metadataTab)
      })
    );

    expect(screen.getByText("当前已发布")).toBeInTheDocument();
    expect(screen.queryByText("待确认的新版本")).not.toBeInTheDocument();
    expect(
      screen.getByText("Currently published description.", {
        selector: ".publication-current-view > div > p"
      })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "编辑发布信息" })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: copy.episode.prepareMetadataRevision
      })
    ).not.toBeInTheDocument();
  });

  it("enables save only after publication information changes", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push(`${init?.method ?? "GET"} ${url}`);
        if (url.includes("/processing-detail")) {
          return Promise.resolve(
            new Response(JSON.stringify(processingPayload(false)), {
              status: 200
            })
          );
        }
        return Promise.resolve(
          new Response(JSON.stringify(publishedEpisodePayload()), {
            status: 200
          })
        );
      })
    );
    const user = userEvent.setup();
    render(
      <PlayerProvider>
        <EpisodeDetail
          onNavigate={() => undefined}
          episodeId={EPISODE_ID}
        />
      </PlayerProvider>
    );

    await user.click(
      await screen.findByRole("tab", {
        name: new RegExp(copy.episode.metadataTab)
      })
    );
    await user.click(
      screen.getByRole("button", { name: "编辑发布信息" })
    );
    const save = await screen.findByRole("button", {
      name: copy.episode.metadataSave
    });
    expect(save).toBeDisabled();

    await user.type(
      await screen.findByLabelText(copy.episode.publicationDescriptionLabel),
      " 新增说明"
    );
    expect(
      calls.filter((call) => call === `GET /api/v1/episodes/${EPISODE_ID}`)
    ).toHaveLength(2);
    expect(
      screen.getByRole("img", { name: copy.episode.artworkPreviewAlt })
    ).toBeInTheDocument();
    expect(save).toBeEnabled();

    await user.click(
      screen.getByRole("button", { name: "取消" })
    );
    expect(screen.getByText("当前已发布")).toBeInTheDocument();
    expect(calls.some((call) => call.startsWith("PATCH "))).toBe(false);
  });

  it("does not open editing when the refreshed episode is already pending", async () => {
    let episodeLoads = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/processing-detail")) {
          return Promise.resolve(
            new Response(JSON.stringify(processingPayload(false)), {
              status: 200
            })
          );
        }
        episodeLoads += 1;
        return Promise.resolve(
          new Response(
            JSON.stringify(
              episodeLoads === 1
                ? publishedEpisodePayload()
                : pendingEpisodePayload()
            ),
            { status: 200 }
          )
        );
      })
    );
    const user = userEvent.setup();
    render(
      <PlayerProvider>
        <EpisodeDetail
          onNavigate={() => undefined}
          episodeId={EPISODE_ID}
        />
      </PlayerProvider>
    );
    await user.click(
      await screen.findByRole("tab", {
        name: new RegExp(copy.episode.metadataTab)
      })
    );
    await user.click(
      screen.getByRole("button", { name: copy.episode.editPublicationMetadata })
    );

    expect(
      await screen.findByText(copy.episode.metadataRevisionAlreadyPending)
    ).toBeInTheDocument();
    expect(
      screen.queryByLabelText(copy.episode.publicationDescriptionLabel)
    ).not.toBeInTheDocument();
    expect(screen.getByText(copy.episode.metadataPendingTitle)).toBeInTheDocument();
  });

  it("edits chapter positions with readable clock values", async () => {
    let submitted: Record<string, unknown> | null = null;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (init?.method === "PATCH") {
          submitted = JSON.parse(String(init.body));
          return Promise.resolve(
            new Response(
              JSON.stringify({
                id: EPISODE_ID,
                revision_version: 2,
                state: "revision_pending",
                changed: true
              }),
              { status: 200 }
            )
          );
        }
        if (url.includes("/processing-detail")) {
          return Promise.resolve(
            new Response(JSON.stringify(processingPayload(false)), {
              status: 200
            })
          );
        }
        return Promise.resolve(
          new Response(JSON.stringify(publishedEpisodePayload()), {
            status: 200
          })
        );
      })
    );
    const user = userEvent.setup();
    render(
      <PlayerProvider>
        <EpisodeDetail
          onNavigate={() => undefined}
          episodeId={EPISODE_ID}
        />
      </PlayerProvider>
    );

    await user.click(
      await screen.findByRole("tab", {
        name: new RegExp(copy.episode.metadataTab)
      })
    );
    await user.click(
      screen.getByRole("button", { name: "编辑发布信息" })
    );
    const thirdTime = await screen.findByLabelText(
      `${copy.episode.chapterTimeLabel} 3`
    );
    expect(thirdTime).toHaveValue("6:00");
    await user.clear(thirdTime);
    await user.type(thirdTime, "7:00");
    await user.click(
      screen.getByRole("button", { name: copy.episode.metadataSave })
    );

    expect(submitted).toMatchObject({
      chapters: [
        { start_seconds: 0, title: "Opening" },
        { start_seconds: 180, title: "Research" },
        { start_seconds: 420, title: "Outlook" }
      ]
    });
  });

  it("keeps save failures inside the publication editor", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (init?.method === "PATCH") {
          return Promise.resolve(
            new Response(JSON.stringify({ detail: "Revision conflict" }), {
              status: 409
            })
          );
        }
        if (url.includes("/processing-detail")) {
          return Promise.resolve(
            new Response(JSON.stringify(processingPayload(false)), {
              status: 200
            })
          );
        }
        return Promise.resolve(
          new Response(JSON.stringify(publishedEpisodePayload()), {
            status: 200
          })
        );
      })
    );
    const user = userEvent.setup();
    render(
      <PlayerProvider>
        <EpisodeDetail
          onNavigate={() => undefined}
          episodeId={EPISODE_ID}
        />
      </PlayerProvider>
    );
    await user.click(
      await screen.findByRole("tab", {
        name: new RegExp(copy.episode.metadataTab)
      })
    );
    await user.click(
      screen.getByRole("button", { name: copy.episode.editPublicationMetadata })
    );
    const description = await screen.findByLabelText(
      copy.episode.publicationDescriptionLabel
    );
    await user.type(description, " New details");
    await user.click(
      screen.getByRole("button", { name: copy.episode.metadataSave })
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Revision conflict"
    );
    expect(description).toBeInTheDocument();
  });

  it("explains invalid chapter spacing before save", async () => {
    vi.stubGlobal("confirm", vi.fn(() => false));
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/processing-detail")) {
          return Promise.resolve(
            new Response(JSON.stringify(processingPayload(false)), {
              status: 200
            })
          );
        }
        return Promise.resolve(
          new Response(JSON.stringify(publishedEpisodePayload()), {
            status: 200
          })
        );
      })
    );
    const user = userEvent.setup();
    render(
      <PlayerProvider>
        <EpisodeDetail
          onNavigate={() => undefined}
          episodeId={EPISODE_ID}
        />
      </PlayerProvider>
    );
    await user.click(
      await screen.findByRole("tab", {
        name: new RegExp(copy.episode.metadataTab)
      })
    );
    await user.click(
      screen.getByRole("button", { name: "编辑发布信息" })
    );
    const thirdTime = await screen.findByLabelText(
      `${copy.episode.chapterTimeLabel} 3`
    );
    await user.clear(thirdTime);
    await user.type(thirdTime, "4:30");

    expect(
      screen.getByText("相邻章节至少需要间隔 2 分钟。")
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: copy.episode.metadataSave })
    ).toBeDisabled();

    await user.click(
      screen.getByRole("tab", { name: new RegExp(copy.episode.scriptTab) })
    );

    expect(window.confirm).toHaveBeenCalled();
    expect(thirdTime).toBeInTheDocument();
  });

  it("keeps unsaved publication edits when tab navigation is cancelled", async () => {
    vi.stubGlobal("confirm", vi.fn(() => false));
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/processing-detail")) {
          return Promise.resolve(
            new Response(JSON.stringify(processingPayload(false)), {
              status: 200
            })
          );
        }
        return Promise.resolve(
          new Response(JSON.stringify(publishedEpisodePayload()), {
            status: 200
          })
        );
      })
    );
    const user = userEvent.setup();
    render(
      <PlayerProvider>
        <EpisodeDetail
          onNavigate={() => undefined}
          episodeId={EPISODE_ID}
        />
      </PlayerProvider>
    );
    await user.click(
      await screen.findByRole("tab", {
        name: new RegExp(copy.episode.metadataTab)
      })
    );
    await user.click(
      screen.getByRole("button", { name: "编辑发布信息" })
    );
    const description = await screen.findByLabelText(
      copy.episode.publicationDescriptionLabel
    );
    await user.type(description, " 尚未保存");

    await user.click(
      screen.getByRole("tab", { name: new RegExp(copy.episode.scriptTab) })
    );

    expect(window.confirm).toHaveBeenCalled();
    expect(description).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: new RegExp(copy.episode.metadataTab) })
    ).toHaveAttribute("aria-selected", "true");
  });

  it("does not leave the episode when unsaved edits are kept", async () => {
    const navigate = vi.fn();
    vi.stubGlobal("confirm", vi.fn(() => false));
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/processing-detail")) {
          return Promise.resolve(
            new Response(JSON.stringify(processingPayload(false)), {
              status: 200
            })
          );
        }
        return Promise.resolve(
          new Response(JSON.stringify(publishedEpisodePayload()), {
            status: 200
          })
        );
      })
    );
    const user = userEvent.setup();
    render(
      <PlayerProvider>
        <EpisodeDetail onNavigate={navigate} episodeId={EPISODE_ID} />
      </PlayerProvider>
    );
    await user.click(
      await screen.findByRole("tab", {
        name: new RegExp(copy.episode.metadataTab)
      })
    );
    await user.click(
      screen.getByRole("button", { name: "编辑发布信息" })
    );
    await user.type(
      await screen.findByLabelText(copy.episode.publicationDescriptionLabel),
      " 尚未保存"
    );

    await user.click(screen.getByRole("button", { name: copy.common.back }));

    expect(window.confirm).toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("shows a compact pending state only for a real revision", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/processing-detail")) {
          return Promise.resolve(
            new Response(JSON.stringify(processingPayload(false)), {
              status: 200
            })
          );
        }
        return Promise.resolve(
          new Response(JSON.stringify(pendingEpisodePayload()), {
            status: 200
          })
        );
      })
    );
    const user = userEvent.setup();
    render(
      <PlayerProvider>
        <EpisodeDetail
          onNavigate={() => undefined}
          episodeId={EPISODE_ID}
        />
      </PlayerProvider>
    );

    await user.click(
      await screen.findByRole("tab", {
        name: new RegExp(copy.episode.metadataTab)
      })
    );

    expect(screen.getByText("更新正在等待你的确认")).toBeInTheDocument();
    expect(
      screen.getByText("当前版本仍保持发布，音频和播放地址不会改变。")
    ).toBeInTheDocument();
    expect(screen.getByText("详细简介、章节")).toBeInTheDocument();
    expect(
      screen.getByText("Currently published description.", {
        selector: ".publication-current-view > div > p"
      })
    ).toBeInTheDocument();
    await user.click(
      screen.getByText(copy.episode.metadataViewChanges)
    );
    expect(
      screen.getByText("Pending revised description.", {
        selector: ".publication-change-details p"
      })
    ).toBeInTheDocument();
    expect(
      screen.getByText((_, element) =>
        Boolean(
          element?.matches(".publication-change-details p") &&
            element.textContent?.includes("4:00 Revised research")
        )
      )
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "编辑发布信息" })
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: copy.episode.takeDown })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: copy.episode.sendPublish })
    ).not.toBeInTheDocument();
  });

  it("does not describe an audio revision as a metadata-only update", async () => {
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/processing-detail")) {
          return Promise.resolve(
            new Response(JSON.stringify(processingPayload(false)), {
              status: 200
            })
          );
        }
        return Promise.resolve(
          new Response(JSON.stringify(audioPendingEpisodePayload()), {
            status: 200
          })
        );
      })
    );
    const user = userEvent.setup();
    render(
      <PlayerProvider>
        <EpisodeDetail
          onNavigate={() => undefined}
          episodeId={EPISODE_ID}
        />
      </PlayerProvider>
    );

    await user.click(
      await screen.findByRole("tab", {
        name: new RegExp(copy.episode.metadataTab)
      })
    );

    expect(
      screen.getByText(copy.episode.audioRevisionPendingTitle)
    ).toBeInTheDocument();
    expect(
      screen.getByText(copy.episode.audioRevisionPendingHint)
    ).toBeInTheDocument();
    expect(
      screen.queryByText(copy.episode.metadataPendingHint)
    ).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: copy.common.play })
    );
    expect(document.querySelector("audio")?.src).toContain(
      `/api/v1/episodes/${EPISODE_ID}/preview?published=true`
    );
  });

  it("takes a published episode down from the private feed", async () => {
    const calls: string[] = [];
    vi.stubGlobal("confirm", vi.fn(() => true));
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push(`${init?.method ?? "GET"} ${url}`);
        if (init?.method === "DELETE") {
          return Promise.resolve(
            new Response(
              JSON.stringify({
                id: EPISODE_ID,
                state: "deleted",
                recoverable_for_days: 30
              }),
              { status: 200 }
            )
          );
        }
        if (url.includes("/processing-detail")) {
          return Promise.resolve(
            new Response(JSON.stringify(processingPayload(false)), {
              status: 200
            })
          );
        }
        return Promise.resolve(
          new Response(JSON.stringify(publishedEpisodePayload()), {
            status: 200
          })
        );
      })
    );

    const user = userEvent.setup();
    render(
      <PlayerProvider>
        <EpisodeDetail
          onNavigate={() => undefined}
          episodeId={EPISODE_ID}
        />
      </PlayerProvider>
    );

    await user.click(
      await screen.findByRole("button", {
        name: copy.episode.takeDown
      })
    );

    expect(calls).toContain(
      `DELETE /api/v1/episodes/${EPISODE_ID}`
    );
    expect(
      await screen.findByText(copy.episode.takeDownSuccess)
    ).toBeInTheDocument();
  });

  it("restores a taken-down episode to the private feed", async () => {
    const calls: string[] = [];
    vi.stubGlobal("confirm", vi.fn(() => true));
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push(`${init?.method ?? "GET"} ${url}`);
        if (url.endsWith(":recover")) {
          return Promise.resolve(
            new Response(
              JSON.stringify({ id: EPISODE_ID, state: "published" }),
              { status: 200 }
            )
          );
        }
        if (url.includes("/processing-detail")) {
          return Promise.resolve(
            new Response(JSON.stringify(processingPayload(false)), {
              status: 200
            })
          );
        }
        return Promise.resolve(
          new Response(
            JSON.stringify({
              ...publishedEpisodePayload(),
              state: "deleted"
            }),
            { status: 200 }
          )
        );
      })
    );

    const user = userEvent.setup();
    render(
      <PlayerProvider>
        <EpisodeDetail
          onNavigate={() => undefined}
          episodeId={EPISODE_ID}
        />
      </PlayerProvider>
    );

    await user.click(
      await screen.findByRole("button", {
        name: copy.episode.recover
      })
    );

    expect(calls).toContain(
      `POST /api/v1/episodes/${EPISODE_ID}:recover`
    );
    expect(
      await screen.findByText(copy.episode.recoverSuccess)
    ).toBeInTheDocument();
  });

  it("does not offer recovery after the recovery window expires", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/processing-detail")) {
          return Promise.resolve(
            new Response(JSON.stringify(processingPayload(false)), {
              status: 200
            })
          );
        }
        return Promise.resolve(
          new Response(
            JSON.stringify({
              ...publishedEpisodePayload(),
              state: "deleted",
              recoverable: false,
              recovery_until: "2026-07-31T00:00:00Z"
            }),
            { status: 200 }
          )
        );
      })
    );

    render(
      <PlayerProvider>
        <EpisodeDetail
          onNavigate={() => undefined}
          episodeId={EPISODE_ID}
        />
      </PlayerProvider>
    );

    expect(
      await screen.findByText(copy.episode.recoveryExpired)
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: copy.episode.recover })
    ).not.toBeInTheDocument();
  });
});
