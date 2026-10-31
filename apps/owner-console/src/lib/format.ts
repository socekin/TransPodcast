/** Player clock: "01:23" under an hour, "1:29:12" from an hour on. */
export function formatClock(seconds: number): string {
  const safe = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const rest = safe % 60;
  const pad = (value: number) => String(value).padStart(2, "0");
  return hours > 0
    ? `${hours}:${pad(minutes)}:${pad(rest)}`
    : `${pad(minutes)}:${pad(rest)}`;
}

/** Episode length for people: "1 小时 29 分", "39 分". */
export function formatLength(seconds: number): string {
  const minutes = Math.max(1, Math.round(seconds / 60));
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) {
    return `${minutes} 分`;
  }
  return rest === 0 ? `${hours} 小时` : `${hours} 小时 ${rest} 分`;
}

/** Elapsed working time: "46 分钟", "2 小时 13 分". */
export function formatElapsed(milliseconds: number): string {
  const minutes = Math.max(0, Math.round(milliseconds / 60000));
  if (minutes < 1) {
    return "不到 1 分钟";
  }
  if (minutes < 60) {
    return `${minutes} 分钟`;
  }
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest === 0 ? `${hours} 小时` : `${hours} 小时 ${rest} 分`;
}

/** Calendar day without the year: "10月24日". */
export function formatDay(value: string | null | undefined): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

/** API timestamps without an offset are UTC. */
export function parseInstant(value: string | null | undefined): number | null {
  if (!value) {
    return null;
  }
  const normalized = /[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`;
  const time = new Date(normalized).getTime();
  return Number.isNaN(time) ? null : time;
}

/** "speaker_0" reads as "说话人 1". */
export function speakerName(label: string): string {
  const match = label.match(/(\d+)$/);
  return match ? `说话人 ${Number(match[1]) + 1}` : label;
}

export function formatRate(rate: number): string {
  return `${rate}×`;
}

export function youtubeVideoId(url: string): string | null {
  try {
    const parsed = new URL(url);
    if (parsed.hostname.endsWith("youtu.be")) {
      return parsed.pathname.slice(1) || null;
    }
    if (parsed.searchParams.get("v")) {
      return parsed.searchParams.get("v");
    }
    const match = parsed.pathname.match(/\/(?:shorts|live|embed)\/([^/?#]+)/);
    return match ? match[1] : null;
  } catch {
    return null;
  }
}

export function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}
