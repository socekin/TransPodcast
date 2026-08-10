import type { View } from "../components/Shell";

export type AppRoute = {
  view: View;
  episodeId: number | null;
  channelId: number | null;
  runId: number | null;
};

const staticPaths: Partial<Record<View, string>> = {
  episodes: "/episodes",
  decisions: "/decisions",
  channels: "/channels",
  "add-channel": "/channels/new",
  interests: "/interests",
  channelVoices: "/channel-voices",
  runs: "/runs",
  settings: "/settings"
};

export function routeFromPath(pathname: string): AppRoute {
  const path =
    pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  const editorMatch = path.match(/^\/episodes\/(demo|\d+)\/edit$/);
  if (editorMatch) {
    const episodeId = parseRouteId(editorMatch[1]);
    return {
      view: episodeId === null ? "episodes" : "episode",
      episodeId,
      channelId: null,
      runId: null
    };
  }

  const episodeMatch = path.match(/^\/episodes\/(demo|\d+)$/);
  if (episodeMatch) {
    const episodeId = parseRouteId(episodeMatch[1]);
    return {
      view: episodeId === null ? "episodes" : "episode",
      episodeId,
      channelId: null,
      runId: null
    };
  }

  if (path === "/channels/new") {
    return {
      view: "add-channel",
      episodeId: null,
      channelId: null,
      runId: null
    };
  }

  const channelMatch = path.match(/^\/channels\/(\d+)$/);
  if (channelMatch) {
    return {
      view: "channel",
      episodeId: null,
      channelId: parseRouteId(channelMatch[1]),
      runId: null
    };
  }

  const runMatch = path.match(/^\/runs\/(\d+)$/);
  if (runMatch) {
    return {
      view: "runs",
      episodeId: null,
      channelId: null,
      runId: parseRouteId(runMatch[1])
    };
  }

  const staticView = Object.entries(staticPaths).find(
    ([, routePath]) => routePath === path
  )?.[0] as View | undefined;

  return {
    view: staticView ?? "episodes",
    episodeId: null,
    channelId: null,
    runId: null
  };
}

export function pathForRoute(route: AppRoute): string {
  if (route.view === "episode") {
    return route.episodeId
      ? `/episodes/${route.episodeId}`
      : staticPaths.episodes!;
  }
  if (route.view === "channel") {
    return route.channelId
      ? `/channels/${route.channelId}`
      : staticPaths.channels!;
  }
  if (route.view === "runs" && route.runId) {
    return `/runs/${route.runId}`;
  }
  return staticPaths[route.view] ?? staticPaths.episodes!;
}

function parseRouteId(value: string | undefined): number | null {
  if (!value || value === "demo") {
    return null;
  }
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}
