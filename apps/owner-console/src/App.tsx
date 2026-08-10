import {
  useCallback,
  useEffect,
  useRef,
  useState
} from "react";

import { Shell, type View } from "./components/Shell";
import { copy } from "./i18n/zh-CN";
import { api, type ApiCandidate } from "./lib/api";
import { PlayerProvider } from "./lib/player";
import {
  pathForRoute,
  routeFromPath,
  type AppRoute
} from "./lib/routing";
import { useApiResource } from "./lib/useApiResource";
import {
  AddChannel,
  ChannelDetail,
  Channels,
  DecisionQueue
} from "./screens/DecisionChannelScreens";
import { EpisodeDetail, EpisodeLibrary } from "./screens/LibraryScreens";
import { ChannelVoiceLibrary } from "./screens/ChannelVoiceLibrary";
import { Runs } from "./screens/RunScreens";
import { Interests, SettingsScreen } from "./screens/SystemScreens";

export function App({ initialView }: { initialView?: View }) {
  return (
    <PlayerProvider>
      <AppContent initialView={initialView} />
    </PlayerProvider>
  );
}

function AppContent({ initialView }: { initialView?: View }) {
  const initialRouteRef = useRef<AppRoute>(
    initialView
      ? {
          view: initialView,
          episodeId: null,
          channelId: null,
          runId: null
        }
      : routeFromPath(window.location.pathname)
  );
  const [view, setView] = useState<View>(initialRouteRef.current.view);
  const viewRef = useRef<View>(initialRouteRef.current.view);
  const currentPathRef = useRef(window.location.pathname);
  const publicationEditDirtyRef = useRef(false);
  const [episodeId, setEpisodeIdState] = useState<number | null>(
    initialRouteRef.current.episodeId
  );
  const [channelId, setChannelIdState] = useState<number | null>(
    initialRouteRef.current.channelId
  );
  const [runId, setRunIdState] = useState<number | null>(
    initialRouteRef.current.runId
  );
  const episodeIdRef = useRef(initialRouteRef.current.episodeId);
  const channelIdRef = useRef(initialRouteRef.current.channelId);
  const runIdRef = useRef(initialRouteRef.current.runId);
  const [channelSaved, setChannelSaved] = useState(false);
  const [apiError, setApiError] = useState("");
  const loadDecisions = useCallback(
    (signal: AbortSignal) => api.decisions(signal),
    []
  );
  const decisions = useApiResource<{ items: ApiCandidate[] }>(loadDecisions);
  const decisionCount =
    decisions.status === "ready"
      ? (decisions.data?.items.length ?? 0)
      : null;

  const recordRoute = useCallback((route: AppRoute) => {
    viewRef.current = route.view;
    currentPathRef.current = pathForRoute(route);
    episodeIdRef.current = route.episodeId;
    channelIdRef.current = route.channelId;
    runIdRef.current = route.runId;
  }, []);

  const applyRoute = useCallback((route: AppRoute) => {
    recordRoute(route);
    setEpisodeIdState(route.episodeId);
    setChannelIdState(route.channelId);
    setRunIdState(route.runId);
    setChannelSaved(false);
    setView(route.view);
  }, [recordRoute]);

  const navigate = useCallback(
    (nextView: View) => {
      const route = {
        view: nextView,
        episodeId: episodeIdRef.current,
        channelId: channelIdRef.current,
        runId: nextView === "runs" ? null : runIdRef.current
      };
      if (
        viewRef.current === "episode" &&
        nextView !== "episode" &&
        publicationEditDirtyRef.current
      ) {
        if (!window.confirm(copy.episode.discardPublicationMetadataConfirm)) {
          return;
        }
        publicationEditDirtyRef.current = false;
      }
      recordRoute(route);
      setRunIdState(route.runId);
      const nextPath = pathForRoute(route);
      if (window.location.pathname !== nextPath) {
        window.history.pushState({}, "", nextPath);
      }
      setView(nextView);
    },
    [recordRoute]
  );

  const selectEpisode = useCallback((id: number | null) => {
    episodeIdRef.current = id;
    setEpisodeIdState(id);
  }, []);

  const selectChannel = useCallback((id: number | null) => {
    channelIdRef.current = id;
    setChannelIdState(id);
  }, []);

  const openRun = useCallback((id: number | null) => {
    const route: AppRoute = {
      view: "runs",
      episodeId: episodeIdRef.current,
      channelId: channelIdRef.current,
      runId: id
    };
    recordRoute(route);
    setRunIdState(id);
    const nextPath = pathForRoute(route);
    if (window.location.pathname !== nextPath) {
      if (id === null) {
        window.history.replaceState({}, "", nextPath);
      } else {
        window.history.pushState({}, "", nextPath);
      }
    }
    setView("runs");
  }, [recordRoute]);

  useEffect(() => {
    const canonicalPath = pathForRoute(initialRouteRef.current);
    if (window.location.pathname !== canonicalPath) {
      window.history.replaceState({}, "", canonicalPath);
    }
    currentPathRef.current = canonicalPath;
    const handlePopState = () => {
      const nextRoute = routeFromPath(window.location.pathname);
      const nextPath = pathForRoute(nextRoute);
      if (
        publicationEditDirtyRef.current &&
        nextPath !== currentPathRef.current
      ) {
        if (!window.confirm(copy.episode.discardPublicationMetadataConfirm)) {
          window.history.pushState({}, "", currentPathRef.current);
          return;
        }
        publicationEditDirtyRef.current = false;
      }
      applyRoute(nextRoute);
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, [applyRoute]);

  useEffect(() => {
    const listener = (event: Event) => {
      const detail = (event as CustomEvent<{ error?: string }>).detail;
      setApiError(detail?.error ?? "");
    };
    window.addEventListener("transpodcast:api-state", listener);
    return () => window.removeEventListener("transpodcast:api-state", listener);
  }, []);

  return (
    <Shell
      active={view}
      onNavigate={navigate}
      apiError={apiError}
      decisionCount={decisionCount}
    >
      {view === "episodes" ? (
        <EpisodeLibrary
          onNavigate={navigate}
          onOpenEpisode={selectEpisode}
          onOpenRun={openRun}
          decisionCount={decisionCount}
        />
      ) : null}
      {view === "episode" ? (
        <EpisodeDetail
          episodeId={episodeId}
          onNavigate={navigate}
          onPublicationEditDirtyChange={(dirty) => {
            publicationEditDirtyRef.current = dirty;
          }}
        />
      ) : null}
      {view === "decisions" ? (
        <DecisionQueue resource={decisions} />
      ) : null}
      {view === "channels" ? (
        <Channels
          onNavigate={navigate}
          onOpenChannel={(id) => {
            selectChannel(id);
            setChannelSaved(false);
            navigate("channel");
          }}
        />
      ) : null}
      {view === "add-channel" ? (
        <AddChannel
          onNavigate={navigate}
          onSaved={(id) => {
            selectChannel(id);
            setChannelSaved(true);
            navigate("channel");
          }}
        />
      ) : null}
      {view === "channel" ? (
        <ChannelDetail
          channelId={channelId}
          justSaved={channelSaved}
          onNavigate={navigate}
        />
      ) : null}
      {view === "interests" ? <Interests /> : null}
      {view === "channelVoices" ? <ChannelVoiceLibrary /> : null}
      {view === "runs" ? (
        <Runs runId={runId} onOpenRun={openRun} />
      ) : null}
      {view === "settings" ? <SettingsScreen /> : null}
    </Shell>
  );
}
