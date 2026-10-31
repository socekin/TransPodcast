import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";

import { readStorage, writeStorage } from "./utils";

/** Menu order, slowest first. */
export const PLAYBACK_RATES = [0.75, 1, 1.25, 1.5, 1.75, 2] as const;
/** Tapping the compact control walks up from 1× and wraps to 0.75×. */
const CYCLE_ORDER = [1, 1.25, 1.5, 1.75, 2, 0.75];

const RATE_KEY = "transpodcast.playbackRate";
const NOW_PLAYING_KEY = "transpodcast.nowPlaying";
const SAVE_INTERVAL_MS = 5000;

export type Track = {
  episodeId: number;
  sourceUrl: string;
  title: string;
  subtitle: string;
  artworkUrl: string;
  duration: number;
};

type SavedTrack = Track & { position: number };

type PlayerValue = {
  track: Track | null;
  currentTime: number;
  duration: number;
  playing: boolean;
  rate: number;
  error: boolean;
  /** Load the track if it is not the current one, then play it. */
  play: (track: Track, startAt?: number) => void;
  toggle: () => void;
  pause: () => void;
  seek: (seconds: number) => void;
  skip: (seconds: number) => void;
  setRate: (rate: number) => void;
  cycleRate: () => void;
};

const PlayerContext = createContext<PlayerValue | null>(null);

export function nextRate(rate: number): number {
  const index = CYCLE_ORDER.indexOf(rate);
  return CYCLE_ORDER[(index + 1) % CYCLE_ORDER.length] ?? 1;
}

export function storedRate(): number {
  const value = Number(readStorage(RATE_KEY));
  return (PLAYBACK_RATES as readonly number[]).includes(value) ? value : 1;
}

function storedTrack(): SavedTrack | null {
  const raw = readStorage(NOW_PLAYING_KEY);
  if (!raw) {
    return null;
  }
  try {
    const value = JSON.parse(raw) as Partial<SavedTrack>;
    if (
      typeof value.episodeId !== "number" ||
      typeof value.sourceUrl !== "string" ||
      typeof value.title !== "string"
    ) {
      return null;
    }
    return {
      episodeId: value.episodeId,
      sourceUrl: value.sourceUrl,
      title: value.title,
      subtitle: value.subtitle ?? "",
      artworkUrl: value.artworkUrl ?? "",
      duration: Number(value.duration) || 0,
      position: Number(value.position) || 0
    };
  } catch {
    return null;
  }
}

export function sameTrack(left: Track | null, right: Track | null): boolean {
  return (
    left !== null &&
    right !== null &&
    left.episodeId === right.episodeId &&
    left.sourceUrl === right.sourceUrl
  );
}

export function PlayerProvider({ children }: PropsWithChildren) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const restored = useRef<SavedTrack | null>(storedTrack());
  const [track, setTrack] = useState<Track | null>(restored.current);
  const [currentTime, setCurrentTime] = useState(
    restored.current?.position ?? 0
  );
  const [duration, setDuration] = useState(restored.current?.duration ?? 0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRateState] = useState(storedRate);
  const [error, setError] = useState(false);
  const rateRef = useRef(rate);
  const trackRef = useRef<Track | null>(restored.current);
  // The source the element holds; a restored track loads only on play.
  const loadedUrl = useRef<string | null>(null);
  const pendingStart = useRef<number | null>(
    restored.current?.position ?? null
  );
  const lastSaved = useRef(0);

  const save = useCallback((position: number) => {
    const current = trackRef.current;
    if (!current) {
      return;
    }
    lastSaved.current = Date.now();
    writeStorage(
      NOW_PLAYING_KEY,
      JSON.stringify({ ...current, position } satisfies SavedTrack)
    );
  }, []);

  const startElement = useCallback(() => {
    const element = audio.current;
    if (!element) {
      return;
    }
    element.playbackRate = rateRef.current;
    void element.play().catch(() => setError(true));
  }, []);

  const play = useCallback(
    (next: Track, startAt?: number) => {
      const element = audio.current;
      setError(false);
      if (!sameTrack(trackRef.current, next) || loadedUrl.current !== next.sourceUrl) {
        const resume =
          sameTrack(trackRef.current, next) && startAt === undefined
            ? pendingStart.current ?? element?.currentTime ?? 0
            : startAt ?? 0;
        trackRef.current = next;
        setTrack(next);
        setDuration(next.duration);
        setCurrentTime(resume);
        pendingStart.current = resume > 0 ? resume : null;
        loadedUrl.current = next.sourceUrl;
        if (element) {
          element.src = next.sourceUrl;
        }
        save(resume);
      } else if (startAt !== undefined && element) {
        element.currentTime = startAt;
        setCurrentTime(startAt);
      }
      startElement();
    },
    [save, startElement]
  );

  const toggle = useCallback(() => {
    const current = trackRef.current;
    const element = audio.current;
    if (!current || !element) {
      return;
    }
    if (loadedUrl.current !== current.sourceUrl) {
      play(current);
      return;
    }
    if (element.paused) {
      startElement();
    } else {
      element.pause();
    }
  }, [play, startElement]);

  const pause = useCallback(() => {
    audio.current?.pause();
  }, []);

  const seek = useCallback(
    (seconds: number) => {
      const limit = duration > 0 ? duration : Number.MAX_SAFE_INTEGER;
      const next = Math.min(Math.max(0, seconds), limit);
      const element = audio.current;
      if (element && loadedUrl.current === trackRef.current?.sourceUrl) {
        element.currentTime = next;
      } else {
        pendingStart.current = next;
      }
      setCurrentTime(next);
      save(next);
    },
    [duration, save]
  );

  const skip = useCallback(
    (seconds: number) => {
      // Read the element's clock so rapid taps accumulate.
      const element = audio.current;
      const base =
        element && loadedUrl.current === trackRef.current?.sourceUrl
          ? element.currentTime
          : pendingStart.current ?? currentTime;
      seek(base + seconds);
    },
    [currentTime, seek]
  );

  const setRate = useCallback((value: number) => {
    rateRef.current = value;
    setRateState(value);
    writeStorage(RATE_KEY, String(value));
    if (audio.current) {
      audio.current.playbackRate = value;
    }
  }, []);

  const cycleRate = useCallback(() => {
    setRate(nextRate(rateRef.current));
  }, [setRate]);

  useEffect(() => {
    const element = audio.current;
    return () => {
      if (element && trackRef.current && loadedUrl.current) {
        save(element.currentTime);
      }
    };
  }, [save]);

  const value = useMemo<PlayerValue>(
    () => ({
      track,
      currentTime,
      duration,
      playing,
      rate,
      error,
      play,
      toggle,
      pause,
      seek,
      skip,
      setRate,
      cycleRate
    }),
    [
      track,
      currentTime,
      duration,
      playing,
      rate,
      error,
      play,
      toggle,
      pause,
      seek,
      skip,
      setRate,
      cycleRate
    ]
  );

  return (
    <PlayerContext.Provider value={value}>
      {children}
      <audio
        ref={audio}
        preload="metadata"
        data-testid="global-audio"
        onPlay={() => {
          setPlaying(true);
          setError(false);
        }}
        onPause={(event) => {
          setPlaying(false);
          save(event.currentTarget.currentTime);
        }}
        onEnded={() => {
          setPlaying(false);
          setCurrentTime(0);
          save(0);
        }}
        onError={() => {
          if (loadedUrl.current) {
            setPlaying(false);
            setError(true);
          }
        }}
        onLoadedMetadata={(event) => {
          const element = event.currentTarget;
          element.playbackRate = rateRef.current;
          if (pendingStart.current !== null) {
            element.currentTime = pendingStart.current;
            pendingStart.current = null;
          }
        }}
        onDurationChange={(event) => {
          const measured = event.currentTarget.duration;
          if (Number.isFinite(measured) && measured > 0) {
            setDuration(measured);
          }
        }}
        onRateChange={(event) => {
          // A new source resets the element's rate to its default.
          if (event.currentTarget.playbackRate !== rateRef.current) {
            event.currentTarget.playbackRate = rateRef.current;
          }
        }}
        onTimeUpdate={(event) => {
          const time = event.currentTarget.currentTime;
          setCurrentTime(time);
          if (Date.now() - lastSaved.current > SAVE_INTERVAL_MS) {
            save(time);
          }
        }}
      />
    </PlayerContext.Provider>
  );
}

export function usePlayer(): PlayerValue {
  const value = useContext(PlayerContext);
  if (!value) {
    throw new Error("usePlayer must be used inside PlayerProvider");
  }
  return value;
}
