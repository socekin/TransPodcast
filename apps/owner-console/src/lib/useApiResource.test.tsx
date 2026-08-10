import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useCallback } from "react";
import { describe, expect, it, vi } from "vitest";

import { useApiResource } from "./useApiResource";

function ResourceProbe({
  load
}: {
  load: (signal: AbortSignal) => Promise<{ title: string }>;
}) {
  const stableLoad = useCallback(load, [load]);
  const resource = useApiResource(stableLoad);
  return (
    <div>
      <span>{resource.status}</span>
      <span>{resource.data?.title ?? "no-data"}</span>
      <span>{resource.error}</span>
      <button type="button" onClick={resource.reload}>
        Retry
      </button>
    </div>
  );
}

describe("useApiResource", () => {
  it("reports a real error without substituting sample data and can retry", async () => {
    const load = vi
      .fn<(signal: AbortSignal) => Promise<{ title: string }>>()
      .mockRejectedValueOnce(new Error("Owner API unavailable"))
      .mockResolvedValueOnce({ title: "Real episode" });
    const user = userEvent.setup();

    render(<ResourceProbe load={load} />);

    expect(await screen.findByText("error")).toBeInTheDocument();
    expect(screen.getByText("no-data")).toBeInTheDocument();
    expect(screen.getByText("Owner API unavailable")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("ready")).toBeInTheDocument();
    expect(screen.getByText("Real episode")).toBeInTheDocument();
  });
});
