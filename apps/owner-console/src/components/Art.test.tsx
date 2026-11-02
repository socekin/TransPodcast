import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RingArt } from "./Art";

describe("RingArt", () => {
  it("draws the played part of a timeline as its own stroke", () => {
    const { container } = render(
      <RingArt src={null} progress={0.25} onSeek={() => undefined} label="节目" />
    );
    expect(container.querySelector(".ring-art-track")).not.toBeNull();
    expect(container.querySelector(".ring-art-played")).toHaveAttribute(
      "stroke-dasharray",
      "25 100"
    );
  });

  it("keeps a plain outline when there is no timeline", () => {
    const { container } = render(<RingArt src={null} progress={null} />);
    expect(container.querySelector(".ring-art-track")).toBeNull();
    expect(container.querySelector(".ring-art-played")).toBeNull();
  });
});
