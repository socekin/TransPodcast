import { describe, expect, it } from "vitest";

import { pathForRoute, routeFromPath } from "./routing";

describe("TransPodcast Host routing", () => {
  it.each([
    ["/", { name: "home" }],
    ["/episodes/42", { name: "episode", id: 42 }],
    ["/episodes/42/", { name: "episode", id: 42 }],
    ["/runs/8", { name: "run", id: 8 }],
    ["/settings", { name: "settings" }]
  ] as const)("restores %s", (path, route) => {
    expect(routeFromPath(path)).toEqual(route);
    expect(pathForRoute(routeFromPath(path))).toBe(path.replace(/\/$/, "") || "/");
  });

  it("opens the episode from a retired Segment Editor URL", () => {
    expect(routeFromPath("/episodes/42/edit")).toEqual({
      name: "episode",
      id: 42
    });
  });

  it.each([
    "/episodes",
    "/episodes/demo",
    "/decisions",
    "/channels",
    "/channels/new",
    "/channels/18",
    "/interests",
    "/channel-voices",
    "/runs",
    "/runs/0",
    "/missing"
  ])("sends the retired or unknown path %s home", (path) => {
    expect(routeFromPath(path)).toEqual({ name: "home" });
  });
});
