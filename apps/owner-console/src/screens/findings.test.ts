import { describe, expect, it } from "vitest";

import { findingDetail, findingOrdinal } from "./findings";

function finding(detail: string, unitId = "seg-0517") {
  return {
    unit_id: unitId,
    parent_segment_id: unitId.split("-retained-")[0],
    kind: "high_cer",
    severity: "hard",
    detail,
    output_start_seconds: 1,
    output_end_seconds: 2
  };
}

describe("Listening Audit findings", () => {
  it.each([
    ["CER 0.50", "错字率 0.50"],
    ["CER 2.00 (short line)", "错字率 2.00（短句，只提示）"],
    ["last 1 target characters missing", "结尾少了 1 个字"],
    ["transcript 5 vs target 2 characters (short line)", "回听 5 个字，译文 2 个字（短句，只提示）"],
    ["untranslated English: p h p", "回听到未翻译的“p h p”"],
    ["8.1 hanzi/s", "每秒 8.1 个字"],
    ["1.4s silence", "停顿 1.4 秒"],
    ["9.0s for 6 hanzi", "6 个字读了 9.0 秒"],
    ["something new", "something new"]
  ])("says %s in Chinese", (detail, expected) => {
    expect(findingDetail(finding(detail))).toBe(expected);
  });

  it("reads the line number from a retained crosstalk piece", () => {
    expect(findingOrdinal(finding("CER 0.50", "seg-0021-retained-1"))).toBe(21);
  });
});
