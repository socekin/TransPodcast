import {
  Bot,
  Library,
  ListChecks,
  Mic2,
  Pause,
  Play,
  Radio,
  RotateCcw,
  RotateCw,
  Settings,
  Sparkles
} from "lucide-react";
import type { ComponentType, PropsWithChildren } from "react";

import { copy } from "../i18n/zh-CN";
import { formatPlayerTime, usePlayer } from "../lib/player";
import { cn } from "../lib/utils";

export type View =
  | "episodes"
  | "episode"
  | "decisions"
  | "channels"
  | "add-channel"
  | "channel"
  | "interests"
  | "channelVoices"
  | "runs"
  | "settings";

type NavItem = {
  id: View;
  label: string;
  icon: ComponentType<{ size?: number; "aria-hidden"?: boolean }>;
  count?: number;
};

const navGroups: { label: string; items: NavItem[] }[] = [
  {
    label: copy.nav.media,
    items: [
      { id: "episodes", label: copy.nav.episodes, icon: Library },
      { id: "decisions", label: copy.nav.decisions, icon: ListChecks },
      { id: "channels", label: copy.nav.channels, icon: Radio }
    ]
  },
  {
    label: copy.nav.intelligence,
    items: [
      { id: "interests", label: copy.nav.interests, icon: Sparkles },
      {
        id: "channelVoices",
        label: copy.nav.channelVoices,
        icon: Mic2
      }
    ]
  },
  {
    label: copy.nav.system,
    items: [
      { id: "runs", label: copy.nav.runs, icon: Bot },
      { id: "settings", label: copy.nav.settings, icon: Settings }
    ]
  }
];

export function Shell({
  active,
  onNavigate,
  children,
  apiError = "",
  decisionCount = null
}: PropsWithChildren<{
  active: View;
  onNavigate: (view: View) => void;
  apiError?: string;
  decisionCount?: number | null;
}>) {
  const player = usePlayer();
  const playerVisible = Boolean(player.title);

  return (
    <div className="application">
      <aside className="sidebar">
        <button
          className="brand"
          type="button"
          onClick={() => onNavigate("episodes")}
        >
          <img
            className="brand-mark"
            src="/assets/transpodcast-artwork.png"
            alt=""
          />
          <span>
            <strong>{copy.brand.name}</strong>
            <small>{copy.brand.subtitle}</small>
          </span>
        </button>

        <nav className="sidebar-nav" aria-label="Primary">
          {navGroups.map((group) => (
            <div className="nav-group" key={group.label}>
              <div className="nav-label">{group.label}</div>
              {group.items.map((item) => {
                const Icon = item.icon;
                const count =
                  item.id === "decisions" ? decisionCount : item.count;
                const isActive =
                  active === item.id ||
                  (item.id === "episodes" &&
                    active === "episode") ||
                  (item.id === "channels" &&
                    ["add-channel", "channel"].includes(active));
                return (
                  <button
                    className={cn("nav-item", isActive && "is-active")}
                    key={item.id}
                    type="button"
                    onClick={() => onNavigate(item.id)}
                  >
                  <Icon size={16} aria-hidden={true} />
                    <span>{item.label}</span>
                    {typeof count === "number" && count > 0 ? (
                      <span className="nav-count">{count}</span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="sidebar-status">
          <span className="avatar">{copy.brand.owner.slice(0, 1)}</span>
          <span>
            <strong>{copy.brand.owner}</strong>
            <small>
              <i className={apiError ? "is-error" : ""} />
              {apiError ? copy.brand.unavailable : copy.brand.health}
            </small>
          </span>
        </div>
      </aside>

      <section className="workspace">
        <main
          className={cn(
            "page",
            playerVisible && "with-player"
          )}
        >
          {apiError ? (
            <div className="api-demo-banner" role="status">
              <strong>{copy.common.apiUnavailable}</strong>
              <span>{copy.common.apiUnavailableHint}</span>
            </div>
          ) : null}
          {children}
        </main>
      </section>

      {playerVisible ? <GlobalPlayer /> : null}
    </div>
  );
}

function GlobalPlayer() {
  const player = usePlayer();
  const progressMax = player.duration > 0 ? player.duration : 1;

  return (
    <aside className="mini-player" aria-label={copy.library.nowPlaying}>
      <img src="/assets/transpodcast-artwork.png" alt="" />
      <span className="mini-player-copy">
        <strong>{player.title}</strong>
        <small>
          {player.error
            ? copy.library.playbackError
            : player.playing
              ? copy.library.nowPlaying
              : copy.library.paused}
        </small>
      </span>
      <button
        className="mini-skip"
        type="button"
        aria-label={copy.library.skipBack}
        onClick={() => player.skip(-15)}
      >
        <RotateCcw size={23} aria-hidden="true" />
        <span>15</span>
      </button>
      <button
        className="mini-player-play"
        type="button"
        aria-label={player.playing ? copy.common.pause : copy.common.play}
        onClick={player.toggle}
      >
        {player.playing ? (
          <Pause size={15} aria-hidden="true" fill="currentColor" />
        ) : (
          <Play size={15} aria-hidden="true" fill="currentColor" />
        )}
      </button>
      <button
        className="mini-skip"
        type="button"
        aria-label={copy.library.skipForward}
        onClick={() => player.skip(15)}
      >
        <RotateCw size={23} aria-hidden="true" />
        <span>15</span>
      </button>
      <span className="mini-time">
        {formatPlayerTime(player.currentTime)}
      </span>
      <input
        className="mini-scrubber"
        aria-label={copy.library.scrubber}
        type="range"
        min="0"
        max={progressMax}
        step="1"
        value={
          player.duration > 0
            ? Math.min(player.currentTime, progressMax)
            : 0
        }
        onChange={(event) => player.seek(Number(event.target.value))}
      />
      <span className="mini-time">
        {formatPlayerTime(player.duration)}
      </span>
      <button
        className="mini-rate"
        type="button"
        aria-label={copy.library.playbackRate}
        onClick={player.cycleRate}
      >
        {player.rate.toFixed(2).replace(/\.?0+$/, "")}×
      </button>
    </aside>
  );
}
