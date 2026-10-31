import { copy } from "../i18n/zh-CN";
import type { ApiListeningAuditFinding } from "../lib/api";

export function findingKind(finding: ApiListeningAuditFinding): string {
  return copy.quality.kinds[finding.kind] ?? finding.kind;
}

/** "seg-0517" (or a retained piece of it) is line 517. */
export function findingOrdinal(finding: ApiListeningAuditFinding): number | null {
  const id = finding.parent_segment_id || finding.unit_id;
  const match = id?.match(/seg-(\d+)/);
  return match ? Number(match[1]) : null;
}

/** The audit writes its evidence in English; say it in Chinese. */
export function findingDetail(finding: ApiListeningAuditFinding): string {
  let detail = finding.detail.trim();
  const shortLine = / \(short line\)$/.test(detail);
  detail = detail.replace(/ \(short line\)$/, "");
  let text = detail;
  let match: RegExpMatchArray | null;
  if ((match = detail.match(/^CER ([\d.]+)$/))) {
    text = `错字率 ${match[1]}`;
  } else if ((match = detail.match(/^last (\d+) target characters missing$/))) {
    text = `结尾少了 ${match[1]} 个字`;
  } else if ((match = detail.match(/^transcript (\d+) vs target (\d+) characters$/))) {
    text = `回听 ${match[1]} 个字，译文 ${match[2]} 个字`;
  } else if ((match = detail.match(/^untranslated English: (.+)$/))) {
    text = `回听到未翻译的“${match[1]}”`;
  } else if ((match = detail.match(/^([\d.]+) hanzi\/s$/))) {
    text = `每秒 ${match[1]} 个字`;
  } else if ((match = detail.match(/^([\d.]+)s silence$/))) {
    text = `停顿 ${match[1]} 秒`;
  } else if ((match = detail.match(/^([\d.]+)s for (\d+) hanzi$/))) {
    text = `${match[2]} 个字读了 ${match[1]} 秒`;
  }
  return shortLine ? `${text}（短句，只提示）` : text;
}
