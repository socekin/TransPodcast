import { useCallback, useEffect, useState } from "react";

import { ShapeDefs } from "./components/Art";
import { LibraryProvider } from "./lib/library";
import { PlayerProvider } from "./lib/player";
import {
  NavigationProvider,
  pathForRoute,
  type Route,
  routeFromPath
} from "./lib/routing";
import { EpisodePage } from "./screens/EpisodePage";
import { Home } from "./screens/Home";
import { RunPage } from "./screens/RunPage";
import { SettingsPage } from "./screens/SettingsPage";

export function App() {
  const [route, setRoute] = useState<Route>(() =>
    routeFromPath(window.location.pathname)
  );

  useEffect(() => {
    // Retired and unknown paths settle on their canonical address.
    const canonical = pathForRoute(routeFromPath(window.location.pathname));
    if (window.location.pathname !== canonical) {
      window.history.replaceState({}, "", canonical);
    }
    const onPop = () => setRoute(routeFromPath(window.location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = useCallback(
    (next: Route, options?: { replace?: boolean }) => {
      const path = pathForRoute(next);
      if (window.location.pathname !== path) {
        if (options?.replace) {
          window.history.replaceState({}, "", path);
        } else {
          window.history.pushState({}, "", path);
        }
      }
      setRoute(next);
      window.scrollTo?.(0, 0);
    },
    []
  );

  return (
    <NavigationProvider navigate={navigate}>
      <LibraryProvider>
        <PlayerProvider>
          <ShapeDefs />
          <main className="app">
            {route.name === "home" ? <Home /> : null}
            {route.name === "episode" ? (
              <EpisodePage key={route.id} id={route.id} />
            ) : null}
            {route.name === "run" ? <RunPage key={route.id} id={route.id} /> : null}
            {route.name === "settings" ? <SettingsPage /> : null}
          </main>
        </PlayerProvider>
      </LibraryProvider>
    </NavigationProvider>
  );
}
