import { afterEach, describe, expect, it } from "vitest";

import { nextRate, PLAYBACK_RATES, storedRate } from "./player";

afterEach(() => window.localStorage.clear());

describe("Playback speed", () => {
  it("steps up from 1× and wraps to 0.75×", () => {
    const seen = [1];
    for (let index = 0; index < PLAYBACK_RATES.length; index += 1) {
      seen.push(nextRate(seen[seen.length - 1]));
    }
    expect(seen).toEqual([1, 1.25, 1.5, 1.75, 2, 0.75, 1]);
  });

  it("restores only a speed the menu offers", () => {
    window.localStorage.setItem("transpodcast.playbackRate", "1.25");
    expect(storedRate()).toBe(1.25);
    window.localStorage.setItem("transpodcast.playbackRate", "3");
    expect(storedRate()).toBe(1);
  });
});
