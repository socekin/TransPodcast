import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useMemo
} from "react";

import { api, type ApiLibraryItem } from "./api";
import { EpisodeLibraryState } from "./generated";
import { type Route } from "./routing";
import { useStableResource } from "./useStableResource";

const ACTIVE_STATES: string[] = [
  EpisodeLibraryState.ACTION_REQUIRED,
  EpisodeLibraryState.PROCESSING,
  EpisodeLibraryState.READY_TO_START,
  EpisodeLibraryState.QUEUED
];

const RUN_STATES: string[] = [...ACTIVE_STATES, EpisodeLibraryState.CLOSED];

type LibraryValue = {
  items: ApiLibraryItem[];
  loading: boolean;
  error: string;
  refresh: () => Promise<unknown>;
};

const LibraryContext = createContext<LibraryValue | null>(null);

export function LibraryProvider({ children }: PropsWithChildren) {
  const load = useCallback(
    (signal: AbortSignal) => api.library(signal),
    []
  );
  const resource = useStableResource(load, {
    poll: true,
    pollIntervalMs: 10000
  });
  const value = useMemo<LibraryValue>(
    () => ({
      items: resource.data?.items ?? [],
      loading: resource.initialLoading,
      error: resource.error,
      refresh: resource.refresh
    }),
    [resource.data, resource.error, resource.initialLoading, resource.refresh]
  );
  return (
    <LibraryContext.Provider value={value}>{children}</LibraryContext.Provider>
  );
}

export function useLibrary(): LibraryValue {
  const value = useContext(LibraryContext);
  if (!value) {
    throw new Error("useLibrary must be used inside LibraryProvider");
  }
  return value;
}

export function isActive(item: ApiLibraryItem): boolean {
  return ACTIVE_STATES.includes(item.library_state);
}

/** Work still owned by its Processing Run opens the run; a finished
 * episode opens the episode. */
export function routeForItem(item: ApiLibraryItem): Route {
  if (RUN_STATES.includes(item.library_state) || item.episode_id === null) {
    return { name: "run", id: item.processing_run_id };
  }
  return { name: "episode", id: item.episode_id };
}

const ACTIVE_ORDER: string[] = [
  EpisodeLibraryState.ACTION_REQUIRED,
  EpisodeLibraryState.READY_TO_START,
  EpisodeLibraryState.PROCESSING,
  EpisodeLibraryState.QUEUED
];

export function activeItems(items: ApiLibraryItem[]): ApiLibraryItem[] {
  return items
    .filter(isActive)
    .sort(
      (left, right) =>
        ACTIVE_ORDER.indexOf(left.library_state) -
          ACTIVE_ORDER.indexOf(right.library_state) ||
        left.created_at.localeCompare(right.created_at)
    );
}
