import {
  createContext,
  type MouseEvent,
  type PropsWithChildren,
  useContext
} from "react";

export type Route =
  | { name: "home" }
  | { name: "episode"; id: number }
  | { name: "run"; id: number }
  | { name: "settings" };

export function routeFromPath(pathname: string): Route {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  // Retired Segment Editor URLs open the matching episode.
  const episode = path.match(/^\/episodes\/(\d+)(?:\/edit)?$/);
  if (episode) {
    const id = parseId(episode[1]);
    return id === null ? { name: "home" } : { name: "episode", id };
  }
  const run = path.match(/^\/runs\/(\d+)$/);
  if (run) {
    const id = parseId(run[1]);
    return id === null ? { name: "home" } : { name: "run", id };
  }
  if (path === "/settings") {
    return { name: "settings" };
  }
  // Retired screens (Decision Queue, channels, interests, voices, the run
  // list) and unknown paths all land on the home screen.
  return { name: "home" };
}

export function pathForRoute(route: Route): string {
  switch (route.name) {
    case "episode":
      return `/episodes/${route.id}`;
    case "run":
      return `/runs/${route.id}`;
    case "settings":
      return "/settings";
    default:
      return "/";
  }
}

function parseId(value: string | undefined): number | null {
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

type Navigate = (route: Route, options?: { replace?: boolean }) => void;

const NavigationContext = createContext<Navigate | null>(null);

export function NavigationProvider({
  navigate,
  children
}: PropsWithChildren<{ navigate: Navigate }>) {
  return (
    <NavigationContext.Provider value={navigate}>
      {children}
    </NavigationContext.Provider>
  );
}

export function useNavigate(): Navigate {
  const navigate = useContext(NavigationContext);
  if (!navigate) {
    throw new Error("useNavigate must be used inside NavigationProvider");
  }
  return navigate;
}

/** Follow an in-app link without a page load, but keep modified clicks
 * (new tab, new window) working like a normal link. */
export function followLink(
  event: MouseEvent<HTMLAnchorElement>,
  navigate: Navigate,
  route: Route
) {
  if (
    event.defaultPrevented ||
    event.button !== 0 ||
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey
  ) {
    return;
  }
  event.preventDefault();
  navigate(route);
}
