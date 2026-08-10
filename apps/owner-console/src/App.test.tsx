import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { copy } from "./i18n/zh-CN";

function ownerSettingsPayload(manualProcessing: boolean) {
  return {
    automation: {
      discovery_interval_minutes: 120,
      duration_guardrail_minutes: 180,
      processing_target_hours: 24,
      manual_processing: manualProcessing,
      auto_discovery: true
    },
    retention: {
      intermediate_days: 7,
      operational_log_days: 7,
      operational_log_max_mb: 500,
      recovery_window_days: 30
    },
    notifications: {},
    budget: {
      spend_guardrail_paused: false,
      authority: "modal_environment"
    },
    secrets: {},
    publication: {
      subscription_url_masked: "https://example.test/feed",
      token_replacement_requires_resubscription: true
    }
  };
}

function episodeDetailPayload(id = 7) {
  return {
    id,
    title: "Real translated episode",
    overview: "A real episode overview.",
    publication_description: "A real publication description.",
    artwork_url: `/api/v1/episodes/${id}/artwork`,
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
    source_show: "Real channel",
    source_title: "Real source episode",
    source_url: "https://www.youtube.com/watch?v=real-episode",
    source_published_at: "2026-08-01T00:00:00Z",
    guests: [],
    created_at: "2026-08-02T00:00:00Z",
    published_at: null,
    preview: { key: "preview", available: true, expires_at: null },
    chapters: [
      { start_seconds: 0, title: "Opening" },
      { start_seconds: 180, title: "Research" },
      { start_seconds: 360, title: "Outlook" }
    ],
    segments: [],
    apple_podcasts_preview: {
      artwork: "artwork.png",
      audio_format: "AAC",
      chapter_count: 3,
      duration_seconds: 3600
    },
    quality_gates: { eligible: true }
  };
}

function stubEpisodeDetail(id = 7) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/decision-queue") {
        return Response.json({ items: [] });
      }
      if (url === `/api/v1/episodes/${id}`) {
        return Response.json(episodeDetailPayload(id));
      }
      if (url === `/api/v1/episodes/${id}/processing-detail`) {
        return Response.json({
          episode_id: id,
          summary: {
            segment_count: 0,
            disputed_count: 0,
            auto_corrected_count: 0,
            pipeline_version: "transpodcast-modal-pipeline-v14",
            content_edit: {
              status: "accepted",
              excluded_count: 0,
              excluded_source_seconds: 0
            }
          },
          speakers: [],
          segments: []
        });
      }
      if (url === `/api/v1/episodes/${id}:notify-publication`) {
        return Response.json({ id, state: "publication_queue" });
      }
      throw new Error(`Unexpected request: ${url}`);
    })
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("TransPodcast Host navigation", () => {
  it("uses the default podcast cover as the sidebar logo", () => {
    const { container } = render(<App />);

    const brand = screen.getByRole("button", {
      name: `${copy.brand.name}${copy.brand.subtitle}`
    });

    expect(brand.querySelector("img")).toHaveAttribute(
      "src",
      "/assets/transpodcast-artwork.png"
    );
    expect(container.querySelector(".topbar")).not.toBeInTheDocument();
    expect(screen.queryByText(copy.common.search)).not.toBeInTheDocument();
  });

  it("shows a deployment-neutral owner identity", () => {
    render(<App />);

    expect(screen.getByText("所有者")).toBeInTheDocument();
    expect(screen.queryByText("Jay")).not.toBeInTheDocument();
  });

  it("starts in the episode library and opens episode detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/decision-queue") {
          return Response.json({ items: [] });
        }
        if (url === "/api/v1/episode-library") {
          return Response.json({
            items: [
              {
                episode_id: 8,
                processing_run_id: 8,
                title: "A real episode",
                overview: "Loaded from the Owner API.",
                state: "preview_ready",
                stage: null,
                duration_seconds: 3600,
                source_show: "A real channel",
                source_title: "A real episode",
                source_url: "https://www.youtube.com/watch?v=real",
                source_published_at: "2026-08-01T00:00:00Z",
                guests: [],
                created_at: "2026-08-01T00:00:00Z",
                published_at: null,
                can_reprocess: true,
                library_state: "processing",
                available_actions: ["view"]
              },
              {
                episode_id: 32,
                processing_run_id: 32,
                title: "Awaiting publication",
                overview: "Ready for an Owner decision.",
                state: "publication_queue",
                stage: null,
                duration_seconds: 1200,
                source_show: "Recovered channel",
                source_title: "Awaiting publication",
                source_url: "https://www.youtube.com/watch?v=awaiting",
                source_published_at: "2026-08-01T00:00:00Z",
                guests: [],
                created_at: "2026-08-01T00:00:00Z",
                published_at: null,
                can_reprocess: true,
                library_state: "ready_to_publish",
                available_actions: ["play", "publish", "decline"]
              },
              {
                episode_id: 33,
                processing_run_id: 33,
                title: "Removed episode",
                overview: "Removed from the Private Feed.",
                state: "deleted",
                stage: null,
                duration_seconds: 1200,
                source_show: "Recovered channel",
                source_title: "Removed episode",
                source_url: "https://www.youtube.com/watch?v=removed",
                source_published_at: "2026-08-01T00:00:00Z",
                guests: [],
                created_at: "2026-08-01T00:00:00Z",
                published_at: null,
                can_reprocess: true,
                library_state: "taken_down",
                available_actions: ["restore"]
              },
              {
                episode_id: 34,
                processing_run_id: 34,
                title: "Future episode",
                overview: "A future lifecycle state.",
                state: "future_internal_state",
                stage: null,
                duration_seconds: 1200,
                source_show: "Recovered channel",
                source_title: "Future episode",
                source_url: "https://www.youtube.com/watch?v=future",
                source_published_at: "2026-08-01T00:00:00Z",
                guests: [],
                created_at: "2026-08-01T00:00:00Z",
                published_at: null,
                can_reprocess: true,
                library_state: "future_internal_state",
                available_actions: []
              }
            ]
          });
        }
        if (url === "/api/v1/processing-queue") {
          return Response.json({ items: [] });
        }
        if (url === "/api/v1/settings") {
          return Response.json(ownerSettingsPayload(false));
        }
        throw new Error(`Unexpected request: ${url}`);
      })
    );
    const user = userEvent.setup();
    render(<App />);

    expect(
      screen.getByRole("heading", { name: copy.library.title })
    ).toBeInTheDocument();
    expect(await screen.findByText(copy.library.readyToPublish)).toBeInTheDocument();
    expect(screen.getByText(copy.library.takenDownState)).toBeInTheDocument();
    expect(screen.getByText(copy.library.unknownState)).toBeInTheDocument();
    expect(screen.queryByText("future_internal_state")).not.toBeInTheDocument();
    await user.click(
      await screen.findByRole("button", { name: "A real episode" })
    );
    expect(window.location.pathname).toBe("/episodes/8");
  });

  it("canonicalizes a retired Segment Editor URL to the same episode", async () => {
    window.history.replaceState({}, "", "/episodes/42/edit");
    const requests: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        requests.push(url);
        if (url === "/api/v1/decision-queue") {
          return Response.json({ items: [] });
        }
        throw new Error("Unavailable in route test");
      })
    );

    render(<App />);

    await waitFor(() => expect(window.location.pathname).toBe("/episodes/42"));
    expect(requests).toContain("/api/v1/episodes/42");
    expect(screen.queryByText("Canonical voice · v3")).not.toBeInTheDocument();
  });

  it("keeps real decision counts aligned across navigation and the library", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/decision-queue") {
          return new Response(JSON.stringify({ items: [] }), { status: 200 });
        }
        if (url === "/api/v1/episode-library") {
          return new Response(
            JSON.stringify({
              items: [
                {
                  episode_id: 8,
                  processing_run_id: 8,
                  title: "Ready episode",
                  overview: "Ready for publication.",
                  state: "publication_queue",
                  stage: null,
                  duration_seconds: 3600,
                  source_show: "Example Show",
                  source_title: "Ready episode",
                  source_url: "https://www.youtube.com/watch?v=ready",
                  source_published_at: "2026-07-31T00:00:00Z",
                  guests: [],
                  created_at: "2026-07-31T00:00:00Z",
                  published_at: null,
                  can_reprocess: true,
                  library_state: "ready_to_publish",
                  available_actions: ["play", "publish", "decline"]
                },
                {
                  episode_id: 9,
                  processing_run_id: 9,
                  title: "Published episode",
                  overview: "Already published.",
                  state: "published",
                  stage: null,
                  duration_seconds: 3600,
                  source_show: "Example Show",
                  source_title: "Published episode",
                  source_url: "https://www.youtube.com/watch?v=published",
                  source_published_at: "2026-07-30T00:00:00Z",
                  guests: [],
                  created_at: "2026-07-30T00:00:00Z",
                  published_at: "2026-08-01T00:00:00Z",
                  can_reprocess: true,
                  library_state: "published",
                  available_actions: ["play"],
                  artwork_url: "/api/v1/episodes/9/artwork"
                }
              ]
            }),
            { status: 200 }
          );
        }
        if (url === "/api/v1/processing-queue") {
          return new Response(JSON.stringify({ items: [] }), { status: 200 });
        }
        if (url === "/api/v1/settings") {
          return new Response(
            JSON.stringify({
              automation: {
                manual_processing: false
              }
            }),
            { status: 200 }
          );
        }
        if (url === "/api/v1/ignored-list") {
          return new Response(JSON.stringify({ items: [] }), { status: 200 });
        }
        throw new Error(`Unexpected request: ${url}`);
      })
    );
    const user = userEvent.setup();
    render(<App initialView="episodes" />);

    expect(
      await screen.findByRole("button", { name: copy.nav.decisions })
    ).toBeInTheDocument();
    const attention = await screen.findByRole("button", {
      name: new RegExp(copy.library.attentionTitleFor(1))
    });
    expect(attention).toHaveTextContent(
      copy.library.attentionBodyFor(0, 1)
    );

    await user.click(attention);

    expect(window.location.pathname).toBe("/episodes");
    expect(screen.getByText("Ready episode")).toBeInTheDocument();
    expect(screen.queryByText("Published episode")).not.toBeInTheDocument();
  });

  it("shows a truthful Episode Library error and recovers without sample rows", async () => {
    let libraryAttempts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/decision-queue") {
          return Response.json({ items: [] });
        }
        if (url === "/api/v1/episode-library") {
          libraryAttempts += 1;
          if (libraryAttempts === 1) {
            return new Response(JSON.stringify({ detail: "Library offline" }), {
              status: 503
            });
          }
          return Response.json({
            items: [
              {
                episode_id: 31,
                processing_run_id: 31,
                title: "Recovered episode",
                overview: "Loaded after retry.",
                state: "published",
                stage: null,
                duration_seconds: 1200,
                source_show: "Recovered channel",
                source_title: "Recovered episode",
                source_url: "https://www.youtube.com/watch?v=recovered",
                source_published_at: "2026-08-01T00:00:00Z",
                guests: [],
                created_at: "2026-08-01T00:00:00Z",
                published_at: "2026-08-02T00:00:00Z",
                can_reprocess: true,
                library_state: "published",
                available_actions: ["play"],
                artwork_url: "/api/v1/episodes/31/artwork"
              }
            ]
          });
        }
        if (url === "/api/v1/processing-queue") {
          return Response.json({ items: [] });
        }
        if (url === "/api/v1/settings") {
          return Response.json(ownerSettingsPayload(false));
        }
        throw new Error(`Unexpected request: ${url}`);
      })
    );
    const user = userEvent.setup();

    render(<App initialView="episodes" />);

    expect(await screen.findByText(copy.library.loadErrorTitle)).toBeInTheDocument();
    expect(document.querySelector(".episode-row")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Filter episodes" })
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: copy.common.retry }));

    expect(await screen.findByText("Recovered episode")).toBeInTheDocument();
    const recoveredRow = screen
      .getByRole("heading", { name: "Recovered episode" })
      .closest("article");
    expect(recoveredRow).not.toBeNull();
    expect(
      within(recoveredRow as HTMLElement).getByText(
        copy.library.publishedState
      )
    ).toBeInTheDocument();
    expect(
      within(recoveredRow as HTMLElement).getByRole("button", {
        name: copy.library.play
      })
    ).toBeInTheDocument();
  });

  it("keeps a failed Source Resolution as a real failure in development", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/decision-queue") {
          return Response.json({ items: [] });
        }
        if (url === "/api/v1/source-resolutions") {
          return new Response(JSON.stringify({ detail: "YouTube unavailable" }), {
            status: 503
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      })
    );
    const user = userEvent.setup();

    render(<App initialView="add-channel" />);
    await user.type(
      screen.getByPlaceholderText(copy.channels.urlPlaceholder),
      "https://www.youtube.com/watch?v=failed"
    );
    await user.click(
      screen.getByRole("button", { name: copy.channels.resolve })
    );

    expect(await screen.findByText("YouTube unavailable")).toBeInTheDocument();
    expect(screen.queryByText("Product Builders")).not.toBeInTheDocument();
    expect(screen.queryByText("demo-video-0")).not.toBeInTheDocument();
  });

  it("recovers the Source Channel list without showing sample channels", async () => {
    let channelAttempts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/decision-queue") {
          return Response.json({ items: [] });
        }
        if (url === "/api/v1/source-channels") {
          channelAttempts += 1;
          if (channelAttempts === 1) {
            return new Response(JSON.stringify({ detail: "Channels offline" }), {
              status: 503
            });
          }
          return Response.json({
            items: [
              {
                id: 91,
                youtube_channel_id: "UC_REAL",
                title: "Real followed channel",
                url: "https://www.youtube.com/@real",
                mode: "agent_filtering",
                avatar_url: ""
              }
            ]
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      })
    );
    const user = userEvent.setup();

    render(<App initialView="channels" />);

    expect(await screen.findByText(copy.channels.loadErrorTitle)).toBeInTheDocument();
    expect(screen.queryByText("Product Builders")).not.toBeInTheDocument();
    expect(screen.queryByText("Research Signals")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: copy.common.retry }));

    expect(await screen.findByText("Real followed channel")).toBeInTheDocument();
  });

  it("restores the current page from the URL and handles browser navigation", async () => {
    window.history.replaceState({}, "", "/settings");
    const user = userEvent.setup();
    render(<App />);

    expect(
      screen.getByRole("heading", { name: copy.settings.title })
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: copy.nav.interests })
    );
    expect(window.location.pathname).toBe("/interests");
    expect(
      screen.getByRole("heading", { name: copy.interests.title })
    ).toBeInTheDocument();

    act(() => {
      window.history.pushState({}, "", "/runs");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(
      screen.getByRole("heading", { name: copy.runs.title })
    ).toBeInTheDocument();
  });

  it("keeps unsaved publication edits across shell and browser navigation", async () => {
    window.history.replaceState({}, "", "/episodes/7");
    vi.stubGlobal("confirm", vi.fn(() => false));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/decision-queue") {
          return new Response(JSON.stringify({ items: [] }), { status: 200 });
        }
        if (url === "/api/v1/episodes/7/processing-detail") {
          return new Response(
            JSON.stringify({
              episode_id: 7,
              summary: {
                segment_count: 0,
                disputed_count: 0,
                auto_corrected_count: 0,
                pipeline_version: "v9",
                content_edit: null
              },
              speakers: [],
              segments: []
            }),
            { status: 200 }
          );
        }
        if (url === "/api/v1/episodes/7") {
          const chapters = [
            { start_seconds: 0, title: "Opening" },
            { start_seconds: 180, title: "Research" },
            { start_seconds: 360, title: "Outlook" }
          ];
          return new Response(
            JSON.stringify({
              id: 7,
              title: "Published episode",
              overview: "Published overview.",
              publication_description: "Published description.",
              artwork_url: "/api/v1/episodes/7/artwork",
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
              state: "published",
              duration_seconds: 3600,
              source_show: "Example Show",
              source_title: "Original episode",
              source_url: "https://www.youtube.com/watch?v=episode",
              source_published_at: "2026-08-01T00:00:00Z",
              guests: [],
              created_at: "2026-08-01T00:30:00Z",
              published_at: "2026-08-01T01:00:00Z",
              recovery_until: null,
              recoverable: false,
              preview: {
                key: "preview.m4a",
                available: true,
                expires_at: "2026-08-02T00:00:00Z"
              },
              chapters,
              segments: [],
              apple_podcasts_preview: {
                artwork: "artwork/transpodcast-3000.png",
                audio_format: "48 kHz stereo AAC-LC · 128 kbps",
                chapter_count: 3,
                duration_seconds: 3600
              },
              quality_gates: { eligible: true },
              published_metadata: {
                title: "Published episode",
                overview: "Published overview.",
                duration_seconds: 3600,
                description: "Published description.",
                chapters,
                artwork_url: "/api/v1/episodes/7/artwork?published=true",
                artwork: { status: "completed" }
              },
              publication_presentation: {
                state: "published_clean",
                revision_kind: "none",
                changed_fields: [],
                backfill_available: false,
                degraded_fields: []
              }
            }),
            { status: 200 }
          );
        }
        throw new Error(`Unexpected request: ${url}`);
      })
    );
    const user = userEvent.setup();
    render(<App />);

    await user.click(
      await screen.findByRole("tab", {
        name: new RegExp(copy.episode.metadataTab)
      })
    );
    await user.click(
      screen.getByRole("button", {
        name: copy.episode.editPublicationMetadata
      })
    );
    const description = await screen.findByLabelText(
      copy.episode.publicationDescriptionLabel
    );
    await user.type(description, " Unsaved");

    await user.click(
      screen.getByRole("button", { name: copy.nav.settings })
    );
    expect(window.confirm).toHaveBeenCalled();
    expect(window.location.pathname).toBe("/episodes/7");
    expect(description).toBeInTheDocument();

    act(() => {
      window.history.pushState({}, "", "/runs");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(window.location.pathname).toBe("/episodes/7");
    expect(description).toBeInTheDocument();
  });

  it("keeps only API-backed controls on the settings page", async () => {
    let savedAutomation: Record<string, unknown> | null = null;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/v1/setup-readiness") {
          return new Response(
            JSON.stringify({
              ready: true,
              services: {
                codex: true,
                modal: true,
                cloudflare: true,
                telegram: false,
                private_feed: true
              },
              details: {
                telegram: {
                  configured: false,
                  impact: "Mobile publication notifications are unavailable.",
                  optional: true
                }
              }
            }),
            { status: 200 }
          );
        }
        if (url === "/api/v1/settings") {
          const automation = {
            discovery_interval_minutes: 120,
            duration_guardrail_minutes: 180,
            processing_target_hours: 24,
            manual_processing: true,
            auto_discovery: false,
            ...(savedAutomation ?? {})
          };
          if (init?.method === "PATCH") {
            savedAutomation = JSON.parse(String(init.body));
          }
          return new Response(
            JSON.stringify({
              automation,
              retention: {
                intermediate_days: 7,
                operational_log_days: 7,
                operational_log_max_mb: 500,
                recovery_window_days: 30
              },
              notifications: {},
              budget: {
                spend_guardrail_paused: false,
                authority: "modal_environment"
              },
              secrets: {},
              publication: {
                subscription_url_masked: "https://example.test/feed",
                token_replacement_requires_resubscription: true
              }
            }),
            { status: 200 }
          );
        }
        throw new Error(`Unexpected request: ${url}`);
      })
    );
    const user = userEvent.setup();
    render(<App initialView="settings" />);

    expect(
      await screen.findByRole("heading", {
        name: copy.settings.automationTitle
      })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: copy.settings.subscription })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: copy.settings.readinessTitle })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("navigation", { name: "Settings categories" })
    ).not.toBeInTheDocument();
    await screen.findByText("https://example.test/feed");
    expect(screen.getByText(copy.settings.optionalMissing)).toBeInTheDocument();

    const interval = screen.getByRole("spinbutton", {
      name: new RegExp(copy.settings.discoveryInterval)
    });
    fireEvent.change(interval, { target: { value: "90" } });
    await user.click(screen.getByRole("button", { name: copy.common.save }));

    await waitFor(() =>
      expect(savedAutomation).toMatchObject({
        discovery_interval_minutes: 90
      })
    );
    expect(screen.getByRole("status")).toHaveTextContent(copy.settings.saved);
  });

  it("keeps publication owner-controlled", async () => {
    window.history.replaceState({}, "", "/episodes/7");
    stubEpisodeDetail();
    const user = userEvent.setup();
    render(<App />);

    await user.click(
      await screen.findByRole("button", { name: copy.episode.sendPublish })
    );
    expect(screen.getByText(copy.episode.sendPublishSuccess)).toBeInTheDocument();
  });

  it("shows the global player after playback starts and preserves it across navigation", async () => {
    window.history.replaceState({}, "", "/episodes/7");
    stubEpisodeDetail();
    vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(
      function (this: HTMLMediaElement) {
        this.dispatchEvent(new Event("play"));
        return Promise.resolve();
      }
    );
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(
      function (this: HTMLMediaElement) {
        this.dispatchEvent(new Event("pause"));
      }
    );
    const user = userEvent.setup();
    render(<App />);

    expect(
      screen.queryByRole("complementary", {
        name: copy.library.nowPlaying
      })
    ).not.toBeInTheDocument();

    await user.click(
      await screen.findByRole("button", { name: copy.common.play })
    );

    let globalPlayer = screen.getByRole("complementary", {
      name: copy.library.nowPlaying
    });
    expect(globalPlayer.querySelector("img")).toHaveAttribute(
      "src",
      "/assets/transpodcast-artwork.png"
    );
    expect(
      within(globalPlayer).getByRole("button", {
        name: copy.common.pause
      })
    ).toBeInTheDocument();

    await user.click(
      within(globalPlayer).getByRole("button", {
        name: copy.library.skipForward
      })
    );
    expect(
      within(globalPlayer).getByRole("slider", {
        name: copy.library.scrubber
      })
    ).toHaveValue("15");

    const playbackRate = within(globalPlayer).getByRole("button", {
      name: copy.library.playbackRate
    });
    await user.click(playbackRate);
    expect(playbackRate).toHaveTextContent("1.25×");

    await user.click(
      screen.getByRole("button", { name: copy.nav.settings })
    );
    globalPlayer = screen.getByRole("complementary", {
      name: copy.library.nowPlaying
    });
    fireEvent.pause(document.querySelector("audio") as HTMLAudioElement);
    expect(
      within(globalPlayer).getByRole("button", {
        name: copy.common.play
      })
    ).toBeInTheDocument();
  });

  it("keeps reprocessing out of the Episode Library rows", async () => {
    const requests: Array<{ url: string; method: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        requests.push({ url, method });
        if (url === "/api/v1/episode-library") {
          return new Response(
            JSON.stringify({
              items: [
                {
                  episode_id: 8,
                  processing_run_id: 8,
                  title: "Robotics at the Edge",
                  overview: "A completed translated episode.",
                  state: "publication_queue",
                  stage: null,
                  duration_seconds: 3600,
                  source_show: "Research Signals",
                  source_title: "Robotics at the Edge",
                  source_url: "https://www.youtube.com/watch?v=useful-008",
                  source_published_at: "2026-07-31T00:00:00Z",
                  guests: [],
                  created_at: "2026-07-31T00:00:00Z",
                  published_at: null,
                  can_reprocess: true,
                  library_state: "ready_to_publish",
                  available_actions: ["play", "publish", "decline"],
                  artwork_url: "/api/v1/episodes/8/artwork"
                }
              ]
            }),
            { status: 200 }
          );
        }
        if (url === "/api/v1/processing-queue") {
          return new Response(JSON.stringify({ items: [] }), { status: 200 });
        }
        if (url === "/api/v1/settings") {
          return new Response(
            JSON.stringify({
              automation: {
                discovery_interval_minutes: 120,
                duration_guardrail_minutes: 180,
                processing_target_hours: 24,
                manual_processing: false,
                auto_discovery: true
              },
              retention: {},
              notifications: {},
              budget: {},
              secrets: {},
              publication: {}
            }),
            { status: 200 }
          );
        }
        if (url === "/api/v1/episodes/8:reprocess" && method === "POST") {
          return new Response(
            JSON.stringify({
              episode_id: 8,
              processing_run_id: 8,
              state: "queued",
              stage: "source_analysis",
              reused_checkpoint: "source_archive"
            }),
            { status: 202 }
          );
        }
        throw new Error(`Unexpected request: ${method} ${url}`);
      })
    );
    render(<App initialView="episodes" />);

    expect(
      await screen.findByRole("heading", { name: "Robotics at the Edge" })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "重新处理" })
    ).not.toBeInTheDocument();
    expect(requests).not.toContainEqual({
      url: "/api/v1/episodes/8:reprocess",
      method: "POST"
    });
  });

  it("publishes a ready episode from the simplified Episode Library", async () => {
    const requests: Array<{ url: string; method: string; body?: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        requests.push({
          url,
          method,
          body: typeof init?.body === "string" ? init.body : undefined
        });
        if (url === "/api/v1/episode-library") {
          return Response.json({
            items: [
              {
                episode_id: 17,
                processing_run_id: 22,
                title: "A qualified Mandarin episode",
                overview: "The exact Private Preview is ready.",
                state: "publication_queue",
                stage: null,
                duration_seconds: 3720,
                source_show: "Useful Show",
                source_title: "A source episode",
                source_url: "https://www.youtube.com/watch?v=useful-017",
                source_published_at: "2026-08-10T00:00:00Z",
                guests: [],
                created_at: "2026-08-10T01:00:00Z",
                published_at: null,
                can_reprocess: true,
                library_state: "ready_to_publish",
                available_actions: ["play", "publish", "decline"],
                artwork_url: "/api/v1/episodes/17/artwork"
              }
            ]
          });
        }
        if (url === "/api/v1/processing-queue") {
          return Response.json({ items: [] });
        }
        if (url === "/api/v1/settings") {
          return Response.json(ownerSettingsPayload(false));
        }
        if (
          url === "/api/v1/episodes/17:decide-publication" &&
          method === "POST"
        ) {
          return Response.json({
            episode_id: 17,
            state: "published",
            decision: "publish",
            channel: "owner_console",
            already_applied: false
          });
        }
        throw new Error(`Unexpected request: ${method} ${url}`);
      })
    );
    const user = userEvent.setup();
    render(<App initialView="episodes" />);

    const title = await screen.findByRole("heading", {
      name: "A qualified Mandarin episode"
    });
    const row = title.closest("article");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("待发布")).toBeInTheDocument();
    expect(
      within(row as HTMLElement).getByRole("button", { name: "播放" })
    ).toBeInTheDocument();
    await user.click(
      within(row as HTMLElement).getByRole("button", { name: "发布" })
    );

    expect(
      screen.getByRole("dialog", { name: "确认发布" })
    ).toBeInTheDocument();
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "发布"
      })
    );

    await waitFor(() =>
      expect(requests).toContainEqual({
        url: "/api/v1/episodes/17:decide-publication",
        method: "POST",
        body: JSON.stringify({
          decision: "publish",
          expected_state: "publication_queue"
        })
      })
    );
    expect(screen.getByText("状态")).toBeInTheDocument();
    expect(screen.getByText("操作")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新处理" })).toBeNull();
  });

  it("renders the exact simplified actions for every Episode Library state", async () => {
    const item = (
      title: string,
      libraryState: string,
      availableActions: string[],
      episodeId: number | null,
      runId: number
    ) => ({
      episode_id: episodeId,
      processing_run_id: runId,
      title,
      overview: `${title} overview`,
      state: libraryState,
      stage: null,
      duration_seconds: 1200,
      source_show: "Example Show",
      source_title: title,
      source_url: `https://www.youtube.com/watch?v=${runId}`,
      source_published_at: "2026-08-10T00:00:00Z",
      guests: [],
      created_at: `2026-08-10T00:00:${String(runId).padStart(2, "0")}Z`,
      published_at: libraryState === "published" ? "2026-08-10T01:00:00Z" : null,
      can_reprocess: episodeId !== null,
      library_state: libraryState,
      available_actions: availableActions,
      artwork_url: episodeId === null ? "" : `/api/v1/episodes/${episodeId}/artwork`
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/decision-queue") {
          return Response.json({ items: [] });
        }
        if (url === "/api/v1/episode-library") {
          return Response.json({
            items: [
              item("Ready item", "ready_to_start", ["start", "remove"], null, 1),
              item("Queued item", "queued", ["view", "remove"], null, 2),
              item("Processing item", "processing", ["view"], 3, 3),
              item("Problem item", "action_required", ["view"], 4, 4),
              item(
                "Publish item",
                "ready_to_publish",
                ["play", "publish", "decline"],
                5,
                5
              ),
              item("Published item", "published", ["play"], 6, 6),
              item("Unpublished item", "unpublished", ["publish"], 7, 7),
              item("Taken down item", "taken_down", ["restore"], 8, 8)
            ]
          });
        }
        if (url === "/api/v1/processing-queue") {
          return Response.json({ items: [] });
        }
        if (url === "/api/v1/settings") {
          return Response.json(ownerSettingsPayload(false));
        }
        throw new Error(`Unexpected request: ${url}`);
      })
    );
    const user = userEvent.setup();
    render(<App initialView="episodes" />);

    const rowFor = async (title: string) => {
      const heading = await screen.findByRole("heading", { name: title });
      const row = heading.closest("article");
      expect(row).not.toBeNull();
      return row as HTMLElement;
    };
    expect(
      within(await rowFor("Ready item")).getByRole("button", {
        name: copy.queue.start
      })
    ).toBeInTheDocument();
    expect(
      within(await rowFor("Queued item")).getByRole("button", {
        name: copy.library.view
      })
    ).toBeInTheDocument();
    expect(
      within(await rowFor("Processing item")).getAllByRole("button")
    ).toHaveLength(2);
    expect(
      within(await rowFor("Problem item")).getByRole("button", {
        name: copy.library.view
      })
    ).toBeInTheDocument();
    const publishRow = await rowFor("Publish item");
    expect(
      within(publishRow).getByRole("button", { name: copy.library.play })
    ).toBeInTheDocument();
    expect(
      within(publishRow).getByRole("button", { name: copy.library.publish })
    ).toBeInTheDocument();
    expect(
      within(await rowFor("Published item")).getByRole("button", {
        name: copy.library.play
      })
    ).toBeInTheDocument();
    expect(
      within(await rowFor("Unpublished item")).getByRole("button", {
        name: copy.library.publish
      })
    ).toBeInTheDocument();
    expect(screen.queryByText("Taken down item")).not.toBeInTheDocument();

    const readyRow = await rowFor("Ready item");
    await user.click(
      within(readyRow).getByRole("button", { name: copy.library.moreActions })
    );
    expect(
      await screen.findByRole("menuitem", { name: copy.library.removeRun })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("menuitem", { name: copy.library.decline })
    ).not.toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(
      within(publishRow).getByRole("button", { name: copy.library.moreActions })
    );
    expect(
      await screen.findByRole("menuitem", { name: copy.library.decline })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("menuitem", { name: copy.library.removeRun })
    ).not.toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(
      screen.getByRole("tab", { name: copy.library.takenDownFilter })
    );
    expect(
      within(await rowFor("Taken down item")).getByRole("button", {
        name: copy.library.restore
      })
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新处理" })).toBeNull();
    expect(screen.queryByRole("button", { name: "试听新版本" })).toBeNull();
    expect(screen.queryByRole("button", { name: "试听当前版本" })).toBeNull();
  });

  it("keeps pending state isolated when two rows mutate concurrently", async () => {
    let finishFirst: (() => void) | undefined;
    let finishSecond: (() => void) | undefined;
    const started = (runId: number) =>
      new Promise<Response>((resolve) => {
        const finish = () =>
          resolve(
            Response.json({ id: runId, state: "queued", stage: "ingest" })
          );
        if (runId === 41) {
          finishFirst = finish;
        } else {
          finishSecond = finish;
        }
      });
    const item = (runId: number, title: string) => ({
      episode_id: null,
      processing_run_id: runId,
      title,
      overview: null,
      state: "pending_start",
      stage: "ingest",
      duration_seconds: 1800,
      source_show: "Example Show",
      source_title: title,
      source_url: `https://www.youtube.com/watch?v=${runId}`,
      source_published_at: "2026-08-10T00:00:00Z",
      guests: [],
      created_at: "2026-08-10T00:00:00Z",
      published_at: null,
      can_reprocess: false,
      library_state: "ready_to_start",
      available_actions: ["start", "remove"],
      artwork_url: ""
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/v1/decision-queue") {
          return Response.json({ items: [] });
        }
        if (url === "/api/v1/episode-library") {
          return Response.json({
            items: [item(41, "First queued episode"), item(42, "Second queued episode")]
          });
        }
        if (url === "/api/v1/processing-queue") {
          return Response.json({ items: [] });
        }
        if (url === "/api/v1/settings") {
          return Response.json(ownerSettingsPayload(true));
        }
        if (url === "/api/v1/processing-runs/41:start" && init?.method === "POST") {
          return started(41);
        }
        if (url === "/api/v1/processing-runs/42:start" && init?.method === "POST") {
          return started(42);
        }
        throw new Error(`Unexpected request: ${url}`);
      })
    );
    const user = userEvent.setup();
    render(<App initialView="episodes" />);
    const firstRow = (await screen.findByRole("heading", {
      name: "First queued episode"
    })).closest("article") as HTMLElement;
    const secondRow = screen
      .getByRole("heading", { name: "Second queued episode" })
      .closest("article") as HTMLElement;
    const firstStart = within(firstRow).getByRole("button", {
      name: copy.queue.start
    });
    const secondStart = within(secondRow).getByRole("button", {
      name: copy.queue.start
    });

    await user.click(firstStart);
    await user.click(secondStart);
    expect(firstStart).toBeDisabled();
    expect(secondStart).toBeDisabled();

    finishFirst?.();
    await waitFor(() => expect(firstStart).not.toBeDisabled());
    expect(secondStart).toBeDisabled();

    finishSecond?.();
    await waitFor(() => expect(secondStart).not.toBeDisabled());
  });

  it("keeps the interest profile focused on Markdown preview and editing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/interest-profile") {
          return Response.json({
            version: 5,
            markdown:
              "# My profile\n\nUse **strong judgment** and *evidence*.\n\n- First item\n\n[Source](https://example.com) and `code`.\n\n<script>window.bad = true</script>",
            active: true
          });
        }
        if (url === "/api/v1/decision-queue") {
          return Response.json({ items: [] });
        }
        throw new Error(`Unexpected request: ${url}`);
      })
    );
    const user = userEvent.setup();
    render(<App initialView="interests" />);

    expect(
      screen.getByRole("heading", { name: copy.interests.title })
    ).toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(await screen.findByText("strong judgment")).toHaveProperty(
      "tagName",
      "STRONG"
    );
    expect(screen.getByText("evidence")).toHaveProperty("tagName", "EM");
    expect(screen.getByRole("link", { name: "Source" })).toHaveAttribute(
      "rel",
      expect.stringContaining("noopener")
    );
    expect(document.querySelector("script")).not.toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: copy.common.edit })
    );
    expect(
      screen.getByRole("textbox", { name: copy.interests.editTitle })
    ).toBeInTheDocument();
  });

  it("uses the candidate YouTube thumbnail in the decision queue", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/decision-queue") {
          return new Response(
            JSON.stringify({
              items: [
                {
                  id: 82,
                  youtube_video_id: "useful-082",
                  title: "Robotics Beyond the Lab",
                  description:
                    "(0:00) Humanoids, robots, and the next industrial platform.",
                  duration_seconds: 4115,
                  source_url:
                    "https://www.youtube.com/watch?v=useful-082",
                  confidence: 0,
                  rationale:
                    "Available Triage Evidence is insufficient.",
                  matched_interests: "[]"
                }
              ]
            }),
            { status: 200 }
          );
        }
        if (url === "/api/v1/ignored-list") {
          return new Response(JSON.stringify({ items: [] }), {
            status: 200
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      })
    );
    render(<App initialView="decisions" />);

    const thumbnail = await screen.findByRole("img", {
      name: "Robotics Beyond the Lab"
    });
    expect(thumbnail).toHaveAttribute(
      "src",
      "/assets/transpodcast-artwork.png"
    );
    expect(
      screen.getByRole("button", { name: copy.decisions.process })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: copy.decisions.ignore })
    ).toBeInTheDocument();
    const decisionNavigation = screen.getByRole("button", {
      name: `${copy.nav.decisions}1`
    });
    expect(within(decisionNavigation).getByText("1")).toBeInTheDocument();
    expect(document.querySelector(".ignored-card")).not.toHaveAttribute(
      "open"
    );
  });

  it("shows a queued Supported Source Video in the Episode Library", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/processing-queue") {
          return new Response(
            JSON.stringify({
              items: [
                {
                  processing_run_id: 1,
                  candidate_video_id: 1,
                  youtube_video_id: "useful-001",
                  title: "Avery Stone on the age of research",
                  source_show: "Research Signals",
                  duration_seconds: 7200,
                  status: "pending_start",
                  stage: "ingest",
                  origin: "manual",
                  created_at: "2026-07-28T14:20:40Z"
                }
              ]
            }),
            { status: 200 }
          );
        }
        if (url === "/api/v1/settings") {
          return new Response(
            JSON.stringify({
              automation: {
                discovery_interval_minutes: 120,
                duration_guardrail_minutes: 180,
                processing_target_hours: 24,
                manual_processing: true
              }
            }),
            { status: 200 }
          );
        }
        if (url !== "/api/v1/episode-library") {
          throw new Error(`Unexpected request: ${url}`);
        }
        return new Response(
          JSON.stringify({
            items: [
              {
                episode_id: null,
                processing_run_id: 1,
                title: "Avery Stone on the age of research",
                overview: null,
                state: "pending_start",
                stage: "ingest",
                duration_seconds: 7200,
                source_show: "Research Signals",
                source_title: "Avery Stone on the age of research",
                source_url:
                  "https://www.youtube.com/watch?v=useful-001",
                source_published_at: "2026-07-28T00:00:00Z",
                guests: [],
                created_at: "2026-07-28T14:20:40Z",
                published_at: null,
                can_reprocess: false,
                library_state: "ready_to_start",
                available_actions: ["start", "remove"],
                artwork_url: "/assets/transpodcast-artwork.png"
              }
            ]
          }),
          { status: 200 }
        );
      })
    );
    const user = userEvent.setup();
    render(<App />);

    expect(
      await screen.findByRole("heading", {
        name: "Avery Stone on the age of research"
      })
    ).toBeInTheDocument();
    // The merged list row carries the manual queue actions directly.
    expect(
      await screen.findByRole("button", { name: copy.queue.start })
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: copy.library.moreActions })
    );
    expect(
      await screen.findByRole("menuitem", { name: copy.library.removeRun })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Avery Stone on the age of research"
      })
    ).toBeInTheDocument();
    expect(screen.getAllByText(copy.library.readyToStart).length).toBeGreaterThan(
      0
    );

    // The pending tab filters the list and shows the manual-mode
    // banner with the start-all action.
    await user.click(
      screen.getByRole("tab", { name: `${copy.queue.title} 1` })
    );
    expect(
      await screen.findByText(copy.queue.manualBanner)
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: copy.queue.startAll })
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", {
        name: "Avery Stone on the age of research"
      })
    );
    expect(
      screen.getByRole("heading", { name: copy.runs.title })
    ).toBeInTheDocument();
  });

  it("imports a Supported Source Video with progressive resolution", async () => {
    let releaseBackfill: () => void = () => {};
    const backfillGate = new Promise<void>((resolve) => {
      releaseBackfill = resolve;
    });
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (
          url === "/api/v1/source-resolutions" &&
          init?.method === "POST"
        ) {
          return new Response(
            JSON.stringify({
              resolution_id: "res-1",
              expires_at: "2026-07-29T00:15:00Z",
              channel: {
                youtube_channel_id: "UC_TEST",
                title: "Test Podcast",
                url: "https://www.youtube.com/@test",
                avatar_url: "",
                subscriber_count: 12000,
                video_count: 42
              },
              requested_video: {
                youtube_video_id: "older-video",
                title: "The Supported Source Video",
                description: "A complete conversation.",
                duration_seconds: 3600,
                language: "en",
                published_at: "2026-01-01T00:00:00Z",
                source_url:
                  "https://www.youtube.com/watch?v=older-video"
              },
              timings_ms: { resolve: 5 }
            }),
            { status: 200 }
          );
        }
        if (url === "/api/v1/source-resolutions/res-1:backfill") {
          await backfillGate;
          return new Response(
            JSON.stringify({
              resolution_id: "res-1",
              items: [
                {
                  youtube_video_id: "older-video",
                  title: "The Supported Source Video",
                  duration_seconds: 3600,
                  source_url:
                    "https://www.youtube.com/watch?v=older-video",
                  published_at: "2026-01-01T00:00:00Z",
                  view_count: 10000,
                  is_requested: true
                },
                {
                  youtube_video_id: "recent-video",
                  title: "A recent episode",
                  duration_seconds: 1800,
                  source_url:
                    "https://www.youtube.com/watch?v=recent-video",
                  published_at: "2026-07-28T00:00:00Z",
                  view_count: 5000,
                  is_requested: false
                }
              ],
              timings_ms: { flat_playlist: 3, date_enrichment: 4 }
            }),
            { status: 200 }
          );
        }
        if (
          url === "/api/v1/source-channels" &&
          init?.method === "POST"
        ) {
          return new Response(
            JSON.stringify({
              id: 1,
              youtube_channel_id: "UC_TEST",
              title: "Test Podcast",
              url: "https://www.youtube.com/@test",
              mode: "agent_filtering",
              avatar_url: "",
              imported_candidate_ids: [1]
            }),
            { status: 201 }
          );
        }
        if (url === "/api/v1/source-channels/1") {
          return new Response(
            JSON.stringify({
              id: 1,
              youtube_channel_id: "UC_TEST",
              title: "Test Podcast",
              url: "https://www.youtube.com/@test",
              mode: "agent_filtering",
              avatar_url: ""
            }),
            { status: 200 }
          );
        }
        if (url === "/api/v1/source-channels/1/recent-videos") {
          return new Response(JSON.stringify({ items: [] }), {
            status: 200
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      }
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App initialView="add-channel" />);

    const sourceUrlInput = screen.getByPlaceholderText(
      copy.channels.urlPlaceholder
    );
    await user.type(
      sourceUrlInput,
      "https://www.youtube.com/watch?v=older-video"
    );
    await user.click(
      screen.getByRole("button", { name: copy.channels.resolve })
    );

    // The requested video and Source Channel render before the
    // Channel Backfill response exists, and save is already enabled.
    expect(
      await screen.findByText("The Supported Source Video")
    ).toBeInTheDocument();
    expect(screen.getByText("Test Podcast")).toBeInTheDocument();
    expect(
      screen.getByText(copy.channels.backfillLoading)
    ).toBeInTheDocument();
    const saveButton = screen.getByRole("button", {
      name: /保存并加入队列/
    });
    expect(saveButton).toBeEnabled();
    expect(saveButton).toHaveTextContent("(1)");

    releaseBackfill();
    const recentCheckbox = await screen.findByRole("checkbox", {
      name: /A recent episode/
    });
    expect(recentCheckbox).not.toBeChecked();

    await user.click(saveButton);
    expect(
      await screen.findByText(copy.channelDetail.savedToast)
    ).toBeInTheDocument();

    const createCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input) === "/api/v1/source-channels" &&
        init?.method === "POST"
    );
    const body = JSON.parse(String(createCall?.[1]?.body)) as {
      resolution_id: string;
      backfill_video_ids: string[];
    };
    expect(body.resolution_id).toBe("res-1");
    expect(body.backfill_video_ids).toEqual([]);
  });

  it("adds one resolved Supported Source Video from the Episode Library", async () => {
    let libraryRequests = 0;
    let importRequests = 0;
    let settingsRequests = 0;
    let finishStaleResolution: (() => void) | null = null;
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/v1/decision-queue") {
          return new Response(JSON.stringify({ items: [] }), { status: 200 });
        }
        if (url === "/api/v1/episode-library") {
          libraryRequests += 1;
          return new Response(JSON.stringify({ items: [] }), { status: 200 });
        }
        if (url === "/api/v1/processing-queue") {
          return new Response(JSON.stringify({ items: [] }), { status: 200 });
        }
        if (url === "/api/v1/settings") {
          settingsRequests += 1;
          if (settingsRequests === 1) {
            return new Response(
              JSON.stringify({ detail: "Settings temporarily unavailable" }),
              { status: 503 }
            );
          }
          return new Response(
            JSON.stringify(ownerSettingsPayload(true)),
            { status: 200 }
          );
        }
        if (url === "/api/v1/setup-readiness") {
          return new Response(
            JSON.stringify({ ready: true, services: {}, details: {} }),
            { status: 200 }
          );
        }
        if (
          url === "/api/v1/source-resolutions" &&
          init?.method === "POST"
        ) {
          const response = new Response(
            JSON.stringify({
              resolution_id: "episode-resolution",
              expires_at: "2026-08-09T12:15:00Z",
              channel: {
                youtube_channel_id: "UC_DIRECT",
                title: "Direct Podcast",
                url: "https://www.youtube.com/@direct",
                avatar_url: "",
                subscriber_count: 1000,
                video_count: 20
              },
              requested_video: {
                youtube_video_id: "direct-video",
                title: "A Direct Video",
                description: "A requested conversation.",
                duration_seconds: 1800,
                language: "en",
                published_at: "2026-08-08T00:00:00Z",
                source_url:
                  "https://www.youtube.com/watch?v=direct-video"
              },
              timings_ms: { resolve: 4 }
            }),
            { status: 200 }
          );
          if (String(init.body).includes("stale-video")) {
            return new Promise<Response>((resolve) => {
              finishStaleResolution = () => resolve(response);
            });
          }
          if (String(init.body).includes("failure-video")) {
            return new Response(
              JSON.stringify({ detail: "YouTube temporarily unavailable" }),
              { status: 503 }
            );
          }
          return response;
        }
        if (
          url ===
            "/api/v1/source-resolutions/episode-resolution:import-video" &&
          init?.method === "POST"
        ) {
          importRequests += 1;
          if (importRequests === 1) {
            return new Response(
              JSON.stringify({ detail: "Import temporarily unavailable" }),
              { status: 503 }
            );
          }
          return new Response(
            JSON.stringify({
              candidate_video_id: 23,
              youtube_video_id: "direct-video",
              candidate_status: "processing_queued",
              run_status: "pending_start",
              processing_run_id: 45,
              episode_id: importRequests > 2 ? 12 : null,
              reused: importRequests > 2,
              source_channel: {
                id: 7,
                title: "Direct Podcast",
                mode: "agent_filtering",
                created: true
              }
            }),
            { status: 201 }
          );
        }
        throw new Error(`Unexpected request: ${url}`);
      }
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App initialView="episodes" />);

    const addButton = await screen.findByRole("button", {
      name: copy.library.addEpisode
    });
    expect(
      screen.queryByRole("button", { name: copy.library.discover })
    ).not.toBeInTheDocument();

    await user.click(addButton);
    let dialog = screen.getByRole("dialog", {
      name: copy.library.addEpisodeTitle
    });
    expect(
      within(dialog).getByRole("button", {
        name: copy.library.importEpisode
      })
    ).toBeDisabled();
    await user.click(
      within(dialog).getByRole("button", {
        name: copy.library.resolveEpisode
      })
    );
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      copy.library.videoUrlRequired
    );
    await user.type(
      within(dialog).getByLabelText(copy.library.addEpisodeUrlLabel),
      "https://www.youtube.com/watch?v=stale-video"
    );
    await user.click(
      within(dialog).getByRole("button", {
        name: copy.library.resolveEpisode
      })
    );
    await user.click(
      within(dialog).getAllByRole("button", {
        name: copy.common.cancel
      })[0]
    );
    await user.click(addButton);
    dialog = screen.getByRole("dialog", {
      name: copy.library.addEpisodeTitle
    });
    await act(async () => {
      finishStaleResolution?.();
      await Promise.resolve();
    });
    expect(within(dialog).queryByText("A Direct Video")).not.toBeInTheDocument();
    await user.type(
      within(dialog).getByLabelText(copy.library.addEpisodeUrlLabel),
      "https://www.youtube.com/watch?v=failure-video"
    );
    await user.click(
      within(dialog).getByRole("button", {
        name: copy.library.resolveEpisode
      })
    );
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "YouTube temporarily unavailable"
    );
    await user.clear(
      within(dialog).getByLabelText(copy.library.addEpisodeUrlLabel)
    );
    await user.type(
      within(dialog).getByLabelText(copy.library.addEpisodeUrlLabel),
      "https://www.youtube.com/watch?v=direct-video"
    );
    await user.click(
      within(dialog).getByRole("button", {
        name: copy.library.resolveEpisode
      })
    );

    expect(await within(dialog).findByText("A Direct Video")).toBeInTheDocument();
    expect(within(dialog).getByText("Direct Podcast")).toBeInTheDocument();
    expect(
      within(dialog).getByText(copy.library.processingSettingUnavailable)
    ).toBeInTheDocument();
    await user.click(
      within(dialog).getByRole("button", {
        name: copy.library.retryProcessingSetting
      })
    );
    expect(
      await within(dialog).findByText(copy.library.importManualNote)
    ).toBeInTheDocument();
    await user.click(
      within(dialog).getByRole("button", {
        name: copy.library.importEpisode
      })
    );

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Import temporarily unavailable"
    );
    expect(
      within(dialog).getByRole("button", {
        name: copy.library.importEpisode
      })
    ).toBeEnabled();
    await user.click(
      within(dialog).getByRole("button", {
        name: copy.library.importEpisode
      })
    );

    expect(
      await screen.findByText(copy.library.importedEpisode)
    ).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(libraryRequests).toBeGreaterThan(1);
    expect(
      fetchMock.mock.calls.some(
        ([input]) => String(input) === "/api/v1/discovery-runs"
      )
    ).toBe(false);

    await user.click(addButton);
    const duplicateDialog = screen.getByRole("dialog", {
      name: copy.library.addEpisodeTitle
    });
    await user.type(
      within(duplicateDialog).getByLabelText(copy.library.addEpisodeUrlLabel),
      "https://www.youtube.com/watch?v=direct-video"
    );
    await user.click(
      within(duplicateDialog).getByRole("button", {
        name: copy.library.resolveEpisode
      })
    );
    await within(duplicateDialog).findByText("A Direct Video");
    await user.click(
      within(duplicateDialog).getByRole("button", {
        name: copy.library.importEpisode
      })
    );

    expect(
      await screen.findByText(copy.library.reusedEpisode)
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", {
        name: copy.library.openExistingEpisode
      })
    );
    expect(window.location.pathname).toBe("/episodes/12");
  });

  it("edits Channel Mode while keeping processing start behavior separate", async () => {
    window.history.replaceState({}, "", "/channels/7");
    let persistedMode = "agent_filtering";
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/v1/decision-queue") {
          return new Response(JSON.stringify({ items: [] }), { status: 200 });
        }
        if (url === "/api/v1/source-channels/7") {
          if (init?.method === "PATCH") {
            const nextMode = String(
              (JSON.parse(String(init.body)) as { mode: string }).mode
            );
            if (nextMode === "paused") {
              return new Response(
                JSON.stringify({ detail: "Mode update failed" }),
                { status: 503 }
              );
            }
            persistedMode = nextMode;
            return new Response(
              JSON.stringify({
                id: 7,
                title: "Editable Podcast",
                mode: persistedMode
              }),
              { status: 200 }
            );
          }
          return new Response(
            JSON.stringify({
              id: 7,
              youtube_channel_id: "UC_EDITABLE",
              title: "Editable Podcast",
              url: "https://www.youtube.com/@editable",
              mode: persistedMode,
              avatar_url: ""
            }),
            { status: 200 }
          );
        }
        if (url === "/api/v1/source-channels/7/recent-videos") {
          return new Response(JSON.stringify({ items: [] }), { status: 200 });
        }
        if (url === "/api/v1/settings") {
          return new Response(
            JSON.stringify(ownerSettingsPayload(true)),
            { status: 200 }
          );
        }
        if (url === "/api/v1/setup-readiness") {
          return new Response(
            JSON.stringify({ ready: true, services: {}, details: {} }),
            { status: 200 }
          );
        }
        throw new Error(`Unexpected request: ${url}`);
      }
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);

    await screen.findByText("Editable Podcast");
    const modeGroup = document.querySelector(".channel-mode-grid");
    expect(modeGroup).not.toBeNull();
    const getModeRadios = () =>
      Array.from(
        document.querySelectorAll<HTMLElement>(
          ".channel-mode-grid [role='radio']"
        )
      );
    const radios = getModeRadios();
    expect(radios[0]).toHaveAttribute("aria-checked", "true");
    expect(
      screen.getByText(copy.channelDetail.processingStartManual)
    ).toBeInTheDocument();

    await user.click(radios[1]);

    expect(
      await screen.findByText(copy.channelDetail.modeSaved)
    ).toBeInTheDocument();
    expect(persistedMode).toBe("process_every_video");
    const patchCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input) === "/api/v1/source-channels/7" &&
        init?.method === "PATCH"
    );
    expect(JSON.parse(String(patchCall?.[1]?.body))).toEqual({
      mode: "process_every_video"
    });

    await waitFor(() => {
      const refreshedRadios = getModeRadios();
      expect(refreshedRadios).toHaveLength(3);
      expect(refreshedRadios[1]).toHaveAttribute("aria-checked", "true");
      expect(refreshedRadios[2]).toBeEnabled();
    });
    const refreshedRadios = getModeRadios();
    await user.click(refreshedRadios[2]);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Mode update failed"
    );
    expect(refreshedRadios[1]).toHaveAttribute("aria-checked", "true");

    await user.click(
      screen.getByRole("button", {
        name: copy.channelDetail.openAutomationSettings
      })
    );
    expect(window.location.pathname).toBe("/settings");
  });
});
