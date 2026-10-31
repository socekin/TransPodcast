import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { EpisodeLibraryAction, EpisodeLibraryState } from "./lib/generated";
import { episode, libraryItem, run } from "./test/fixtures";

type Handler = (body: unknown) => unknown;

let routes: Record<string, Handler>;
let calls: Array<{ method: string; path: string; body: unknown }>;

function respond(value: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(value), {
      status,
      headers: { "Content-Type": "application/json" }
    })
  );
}

function installApi(extra: Record<string, Handler> = {}) {
  routes = {
    "GET /api/v1/episode-library": () => ({
      items: [
        libraryItem({
          processing_run_id: 34,
          title: "Harbor Lights Roundtable",
          library_state: EpisodeLibraryState.ACTION_REQUIRED,
          state: "review_queue"
        }),
        libraryItem({
          processing_run_id: 39,
          title: "Quiet Circuits Weekly",
          library_state: EpisodeLibraryState.PROCESSING
        }),
        libraryItem({
          processing_run_id: 8,
          episode_id: 8,
          title: "示例节目一",
          source_show: "Example Show",
          library_state: EpisodeLibraryState.READY_TO_PUBLISH,
          state: "publication_queue",
          available_actions: [
            EpisodeLibraryAction.PLAY,
            EpisodeLibraryAction.PUBLISH,
            EpisodeLibraryAction.DECLINE
          ],
          source_url: "https://www.youtube.com/watch?v=example0001"
        }),
        libraryItem({
          processing_run_id: 35,
          episode_id: 35,
          title: "示例节目二",
          library_state: EpisodeLibraryState.PUBLISHED,
          state: "published",
          published_at: "2026-10-31T02:28:51"
        })
      ]
    }),
    "GET /api/v1/setup-readiness": () => ({
      ready: true,
      services: { agent: true },
      details: { agent: { configured: true, impact: "" } }
    }),
    "GET /api/v1/processing-runs/34": () =>
      run({
        id: 34,
        status: "review_queue",
        owner_state: "action_required",
        title: "Harbor Lights Roundtable",
        current_error: {
          kind: "listening_audit",
          summary: "Listening Audit hard findings remain",
          retryable: true
        },
        stepStates: [
          "completed",
          "completed",
          "completed",
          "completed",
          "completed",
          "completed",
          "action_required",
          "not_started"
        ]
      }),
    "GET /api/v1/processing-runs/39": () => run({ id: 39, title: "Quiet Circuits Weekly" }),
    "GET /api/v1/processing-runs/34/transcript": () => ({
      processing_run_id: 34,
      lines: [
        {
          ordinal: 517,
          segment_id: "seg-0517",
          speaker_label: "speaker_1",
          output_start_seconds: 3000,
          output_end_seconds: 3001,
          source_text: "Thank you.",
          target_text: "谢谢。"
        }
      ]
    }),
    "GET /api/v1/episodes/8": () => episode({}),
    ...extra
  };
  // The decision run carries its audit on the quality step.
  const decisionRun = routes["GET /api/v1/processing-runs/34"];
  routes["GET /api/v1/processing-runs/34"] = (body) => {
    const value = decisionRun(body) as ReturnType<typeof run>;
    value.pipeline_steps[6].metrics = {
      listening_audit: {
        status: "complete",
        policy_version: "listening-audit-v2",
        unit_count: 520,
        hard_count: 1,
        soft_count: 0,
        unavailable_count: 0,
        hard_unit_ids: ["seg-0517"],
        blocking_unit_ids: ["seg-0517"],
        owner_acknowledged: false,
        findings: [
          {
            unit_id: "seg-0517",
            parent_segment_id: "seg-0517",
            kind: "high_cer",
            severity: "hard",
            detail: "CER 0.50",
            output_start_seconds: 3000,
            output_end_seconds: 3001
          }
        ],
        retry: { status: "rendered", unit_ids: ["seg-0517"], resolved_unit_ids: [] }
      }
    };
    return value;
  };
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      const key = `${method} ${url.pathname}`;
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ method, path: url.pathname, body });
      const handler = routes[key];
      return handler
        ? respond(handler(body))
        : respond({ detail: `No fixture for ${key}` }, 404);
    })
  );
}

function visit(path: string) {
  window.history.replaceState({}, "", path);
  return render(<App />);
}

beforeEach(() => {
  window.localStorage.clear();
  vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(function (
    this: HTMLMediaElement
  ) {
    this.dispatchEvent(new Event("play"));
    return Promise.resolve();
  });
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(function (
    this: HTMLMediaElement
  ) {
    this.dispatchEvent(new Event("pause"));
  });
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined);
  installApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Home", () => {
  it("shows work in progress and the episodes waiting to publish", async () => {
    visit("/");

    expect(await screen.findByText("Harbor Lights Roundtable")).toBeInTheDocument();
    expect(screen.getByText("Quiet Circuits Weekly")).toBeInTheDocument();
    expect(
      await screen.findByText("等你确认 · 听感审计有 1 处问题")
    ).toBeInTheDocument();
    expect(
      await screen.findByText(/翻译 · 第 3 步，共 8 步/)
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "待发布 1" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    expect(screen.getByText("示例节目一")).toBeInTheDocument();
    expect(screen.queryByText("示例节目二")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "已发布 1" }));
    expect(screen.getByText("示例节目二")).toBeInTheDocument();
  });

  it("publishes a ready episode from the list", async () => {
    installApi({
      "POST /api/v1/episodes/8:decide-publication": () => ({
        already_applied: false,
        channel: "owner_console",
        decision: "publish",
        episode_id: 8,
        state: "published"
      })
    });
    visit("/");
    await userEvent.click(await screen.findByRole("button", { name: "发布" }));

    await waitFor(() =>
      expect(calls).toContainEqual({
        method: "POST",
        path: "/api/v1/episodes/8:decide-publication",
        body: { decision: "publish", expected_state: "publication_queue" }
      })
    );
  });

  it("sends retired screens home", async () => {
    visit("/decisions");
    await screen.findByText("Harbor Lights Roundtable");
    expect(window.location.pathname).toBe("/");
  });
});

describe("Adding an episode", () => {
  it("starts processing right after the link is added", async () => {
    installApi({
      "POST /api/v1/source-resolutions": () => ({
        resolution_id: "res-1",
        expires_at: "2026-11-01T00:00:00",
        timings_ms: {},
        existing_source_channel: null,
        channel: {
          avatar_url: "",
          subscriber_count: null,
          title: "Fictional Interviews",
          url: "https://www.youtube.com/@fictional-interviews",
          video_count: null,
          youtube_channel_id: "UC1"
        },
        requested_video: {
          description: "",
          duration_seconds: 3360,
          language: "en",
          published_at: "2026-10-30T00:00:00",
          source_url: "https://www.youtube.com/watch?v=newvideo001",
          title: "A new interview",
          youtube_video_id: "newvideo001"
        }
      }),
      "POST /api/v1/source-resolutions/res-1:import-video": () => ({
        candidate_status: "processing",
        candidate_video_id: 50,
        episode_id: null,
        processing_run_id: 50,
        reused: false,
        run_status: "pending_start",
        source_channel: { created: false, id: 3, mode: "paused", title: "Fictional Interviews" },
        youtube_video_id: "newvideo001"
      }),
      "POST /api/v1/processing-runs/50:start": () => ({ id: 50, state: "queued" }),
      "GET /api/v1/processing-runs/50": () =>
        run({ id: 50, status: "queued", owner_state: "queued", title: "A new interview" })
    });
    visit("/");
    await userEvent.type(
      await screen.findByLabelText("节目链接"),
      "https://www.youtube.com/watch?v=newvideo001"
    );
    await userEvent.click(screen.getByRole("button", { name: "开始处理" }));

    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("A new interview")).toBeInTheDocument();
    expect(within(dialog).getByText("Fictional Interviews · 56 分")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "开始处理" }));

    await waitFor(() => expect(window.location.pathname).toBe("/runs/50"));
    expect(calls.map((call) => `${call.method} ${call.path}`)).toContain(
      "POST /api/v1/processing-runs/50:start"
    );
    expect(await screen.findByRole("heading", { name: "排队中" })).toBeInTheDocument();
  });

  it("adds an episode from any link yt-dlp can download", async () => {
    installApi({
      "POST /api/v1/source-resolutions": () => ({
        resolution_id: "res-2",
        expires_at: "2026-11-01T00:00:00",
        timings_ms: {},
        existing_source_channel: null,
        channel: {
          avatar_url: "",
          subscriber_count: null,
          title: "Fictional Podcast",
          url: "https://podcasts.example.com",
          video_count: null,
          youtube_channel_id: "applepodcasts:fictional"
        },
        requested_video: {
          description: "",
          duration_seconds: 2700,
          language: "en",
          platform: "applepodcasts",
          published_at: "2026-10-30T00:00:00",
          source_url: "https://podcasts.example.com/episode-1",
          thumbnail_url: "https://podcasts.example.com/episode-1.jpg",
          title: "A podcast-only episode",
          youtube_video_id: "applepodcasts:1000000000001"
        }
      }),
      "POST /api/v1/source-resolutions/res-2:import-video": () => ({
        candidate_status: "processing",
        candidate_video_id: 51,
        episode_id: null,
        processing_run_id: 51,
        reused: false,
        run_status: "pending_start",
        source_channel: { created: true, id: 4, mode: "paused", title: "Fictional Podcast" },
        youtube_video_id: "applepodcasts:1000000000001"
      }),
      "POST /api/v1/processing-runs/51:start": () => ({ id: 51, state: "queued" }),
      "GET /api/v1/processing-runs/51": () =>
        run({ id: 51, status: "queued", owner_state: "queued", title: "A podcast-only episode" })
    });
    visit("/");
    await userEvent.type(
      await screen.findByLabelText("节目链接"),
      "https://podcasts.example.com/episode-1"
    );
    await userEvent.click(screen.getByRole("button", { name: "开始处理" }));

    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("A podcast-only episode")).toBeInTheDocument();
    expect(dialog.querySelector(".ring-art-image")).toHaveAttribute(
      "src",
      "https://podcasts.example.com/episode-1.jpg"
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "开始处理" }));

    await waitFor(() => expect(window.location.pathname).toBe("/runs/51"));
  });

  it("recognises a non-YouTube episode already in the library by its address", async () => {
    installApi();
    const library = routes["GET /api/v1/episode-library"];
    routes["GET /api/v1/episode-library"] = (body) => {
      const value = library(body) as { items: ReturnType<typeof libraryItem>[] };
      value.items.push(
        libraryItem({
          processing_run_id: 60,
          episode_id: 60,
          title: "Already here",
          library_state: EpisodeLibraryState.PUBLISHED,
          state: "published",
          source_url: "https://podcasts.example.com/episode-9"
        })
      );
      return value;
    };
    visit("/");
    await userEvent.type(
      await screen.findByLabelText("节目链接"),
      "https://www.podcasts.example.com/episode-9/#listen"
    );
    await userEvent.click(screen.getByRole("button", { name: "开始处理" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("这一期已经在节目库里了。")).toBeInTheDocument();
    expect(within(dialog).getByText("Already here")).toBeInTheDocument();
    expect(calls.some((call) => call.path === "/api/v1/source-resolutions")).toBe(false);
  });

  it("recognises an episode already in the library without looking it up", async () => {
    visit("/");
    await userEvent.type(
      await screen.findByLabelText("节目链接"),
      "https://youtu.be/example0001"
    );
    await userEvent.click(screen.getByRole("button", { name: "开始处理" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("这一期已经在节目库里了。")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "开始处理" })).toBeDisabled();
    expect(calls.some((call) => call.path === "/api/v1/source-resolutions")).toBe(false);
  });
});

describe("Playback speed", () => {
  it("applies the chosen speed to every episode and remembers it", async () => {
    visit("/episodes/8");
    await userEvent.click(await screen.findByRole("button", { name: "播放速度 1×" }));
    const menu = screen.getByRole("group", { name: "播放速度" });
    expect(within(menu).getByText("播放速度 · 对所有节目生效")).toBeInTheDocument();
    await userEvent.click(within(menu).getByRole("button", { name: "1.5×" }));

    expect(screen.getByRole("button", { name: "播放速度 1.5×" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "播放速度" })).not.toBeInTheDocument();
    expect(window.localStorage.getItem("transpodcast.playbackRate")).toBe("1.5");

    await userEvent.click(screen.getByRole("button", { name: "试听" }));
    const audio = screen.getByTestId("global-audio") as HTMLAudioElement;
    expect(audio.playbackRate).toBe(1.5);
  });

  it("lets the mini player step through speeds", async () => {
    window.localStorage.setItem("transpodcast.playbackRate", "2");
    window.localStorage.setItem(
      "transpodcast.nowPlaying",
      JSON.stringify({
        episodeId: 35,
        sourceUrl: "/api/v1/episodes/35/preview?published=true",
        title: "示例节目二",
        subtitle: "Synthetic Weekly",
        artworkUrl: "",
        duration: 4121,
        position: 1812
      })
    );
    visit("/");
    const player = await screen.findByRole("region", { name: "继续收听" });
    expect(within(player).getByText("30:12 / 1:08:41")).toBeInTheDocument();

    await userEvent.click(
      within(player).getByRole("button", { name: "播放速度 2×，点按切换" })
    );
    expect(
      within(player).getByRole("button", { name: "播放速度 0.75×，点按切换" })
    ).toBeInTheDocument();
    expect(window.localStorage.getItem("transpodcast.playbackRate")).toBe("0.75");
  });
});

describe("A run that needs the Owner", () => {
  it("auditions the problem line at its own speed and continues on request", async () => {
    window.localStorage.setItem("transpodcast.playbackRate", "1.75");
    installApi({
      "POST /api/v1/processing-runs/34:retry": () =>
        run({ id: 34, status: "queued", owner_state: "queued" })
    });
    visit("/runs/34");

    expect(await screen.findByText("听感审计还有 1 句没过")).toBeInTheDocument();
    expect(screen.getByText("回听文字对不上")).toBeInTheDocument();
    expect(await screen.findByText("谢谢。")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "播放速度 1×" }));
    const menu = screen.getByRole("group", { name: "播放速度" });
    expect(within(menu).getByText("单句试听默认 1×，只对这一句生效")).toBeInTheDocument();
    await userEvent.click(within(menu).getByRole("button", { name: "0.75×" }));
    expect(window.localStorage.getItem("transpodcast.playbackRate")).toBe("1.75");

    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: "试听第 517 句" }));
    });
    expect(screen.getByRole("button", { name: "暂停试听" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "听过了，没问题，继续" }));
    await waitFor(() =>
      expect(calls.map((call) => `${call.method} ${call.path}`)).toContain(
        "POST /api/v1/processing-runs/34:retry"
      )
    );
  });
});
