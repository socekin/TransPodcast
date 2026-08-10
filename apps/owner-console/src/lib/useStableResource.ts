import { useCallback, useEffect, useRef, useState } from "react";

type StableResource<T> = {
  data: T | null;
  initialLoading: boolean;
  refreshing: boolean;
  stale: boolean;
  error: string;
  refresh: () => Promise<T | null>;
  replace: (value: T) => void;
};

export function useStableResource<T>(
  load: (signal: AbortSignal) => Promise<T>,
  {
    poll = false,
    pollIntervalMs = 5000,
    getRevision,
    shouldPoll,
    resourceKey
  }: {
    poll?: boolean;
    pollIntervalMs?: number;
    getRevision?: (value: T) => string | undefined;
    shouldPoll?: (value: T | null) => boolean;
    resourceKey?: string | number;
  } = {}
): StableResource<T> {
  const [data, setData] = useState<T | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [stale, setStale] = useState(false);
  const [error, setError] = useState("");
  const inFlight = useRef<Promise<T | null> | null>(null);
  const controller = useRef<AbortController | null>(null);
  const dataRef = useRef<T | null>(null);
  const revisionRef = useRef<string | undefined>(undefined);
  const resourceKeyRef = useRef(resourceKey);
  const mounted = useRef(true);

  const refresh = useCallback(() => {
    if (inFlight.current) {
      return inFlight.current;
    }
    const requestController = new AbortController();
    controller.current = requestController;
    setRefreshing(true);
    const request = load(requestController.signal)
      .then((value) => {
        if (mounted.current) {
          const revision = getRevision?.(value);
          if (!revision || revision !== revisionRef.current) {
            dataRef.current = value;
            revisionRef.current = revision;
            setData(value);
          }
          setStale(false);
          setError("");
          setInitialLoading(false);
        }
        return value;
      })
      .catch((reason: unknown) => {
        if (!mounted.current || requestController.signal.aborted) {
          return null;
        }
        const message =
          reason instanceof Error ? reason.message : String(reason);
        setError(message);
        setStale(true);
        setInitialLoading(false);
        return null;
      })
      .finally(() => {
        if (mounted.current) {
          setRefreshing(false);
        }
        if (controller.current === requestController) {
          controller.current = null;
          inFlight.current = null;
        }
      });
    inFlight.current = request;
    return request;
  }, [getRevision, load]);

  useEffect(() => {
    if (resourceKeyRef.current !== resourceKey) {
      resourceKeyRef.current = resourceKey;
      dataRef.current = null;
      revisionRef.current = undefined;
      setData(null);
      setInitialLoading(true);
      setStale(false);
      setError("");
    }
    mounted.current = true;
    void refresh();
    return () => {
      mounted.current = false;
      controller.current?.abort();
      controller.current = null;
      inFlight.current = null;
    };
  }, [refresh, resourceKey]);

  useEffect(() => {
    if (!poll) {
      return;
    }
    const pollNow = () => {
      if (
        document.visibilityState === "visible" &&
        (shouldPoll?.(dataRef.current) ?? true)
      ) {
        void refresh();
      }
    };
    const timer = window.setInterval(pollNow, pollIntervalMs);
    document.addEventListener("visibilitychange", pollNow);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", pollNow);
    };
  }, [poll, pollIntervalMs, refresh, shouldPoll]);

  const replace = useCallback(
    (value: T) => {
      controller.current?.abort();
      controller.current = null;
      inFlight.current = null;
      dataRef.current = value;
      revisionRef.current = getRevision?.(value);
      setData(value);
      setInitialLoading(false);
      setStale(false);
      setError("");
    },
    [getRevision]
  );

  return {
    data,
    initialLoading,
    refreshing,
    stale,
    error,
    refresh,
    replace
  };
}
