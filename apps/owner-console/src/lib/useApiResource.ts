import { useCallback, useEffect, useState } from "react";

export type ResourceState<T> = {
  data: T | null;
  status: "loading" | "ready" | "error";
  error: string;
  reload: () => void;
};

export function useApiResource<T>(
  load: (signal: AbortSignal) => Promise<T>
): ResourceState<T> {
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<{
    data: T | null;
    status: ResourceState<T>["status"];
    error: string;
  }>({
    data: null,
    status: "loading",
    error: ""
  });

  useEffect(() => {
    const controller = new AbortController();
    setState((current) => ({ ...current, status: "loading", error: "" }));
    load(controller.signal)
      .then((data) => {
        window.dispatchEvent(
          new CustomEvent("transpodcast:api-state", {
            detail: { error: "" }
          })
        );
        setState({ data, status: "ready", error: "" });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        const message =
          error instanceof Error ? error.message : String(error);
        window.dispatchEvent(
          new CustomEvent("transpodcast:api-state", {
            detail: { error: message }
          })
        );
        setState({
          data: null,
          status: "error",
          error: message
        });
      });
    return () => controller.abort();
  }, [load, revision]);

  const reload = useCallback(() => setRevision((value) => value + 1), []);
  return { ...state, reload };
}
