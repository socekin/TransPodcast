import { describe, expect, it } from "vitest";

import { pathForRoute, routeFromPath } from "./routing";

describe("Owner Console routing", () => {
  it.each([
    ["/episodes", "episodes", null, null, null],
    ["/episodes/42", "episode", 42, null, null],
    ["/episodes/42/edit", "episode", 42, null, null],
    ["/episodes/demo", "episodes", null, null, null],
    ["/decisions", "decisions", null, null, null],
    ["/channels", "channels", null, null, null],
    ["/channels/new", "add-channel", null, null, null],
    ["/channels/18", "channel", null, 18, null],
    ["/interests", "interests", null, null, null],
    ["/channel-voices", "channelVoices", null, null, null],
    ["/runs", "runs", null, null, null],
    ["/runs/8", "runs", null, null, 8],
    ["/settings", "settings", null, null, null]
  ] as const)(
    "restores %s",
    (path, view, episodeId, channelId, runId) => {
      expect(routeFromPath(path)).toEqual({
        view,
        episodeId,
        channelId,
        runId
      });
    }
  );

  it("creates entity-aware detail paths", () => {
    expect(
      pathForRoute({
        view: "episode",
        episodeId: 42,
        channelId: null,
        runId: null
      })
    ).toBe("/episodes/42");
    expect(
      pathForRoute({
        view: "channel",
        episodeId: null,
        channelId: 18,
        runId: null
      })
    ).toBe("/channels/18");
    expect(
      pathForRoute({
        view: "runs",
        episodeId: null,
        channelId: null,
        runId: 8
      })
    ).toBe("/runs/8");
  });

  it("falls back to the episode library for unknown paths", () => {
    expect(routeFromPath("/unknown")).toEqual({
      view: "episodes",
      episodeId: null,
      channelId: null,
      runId: null
    });
  });
});
