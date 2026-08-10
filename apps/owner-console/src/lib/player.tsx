import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useRef,
  useState
} from "react";

const PLAYBACK_RATES = [1, 1.25, 1.5, 1.75, 2, 0.75];

type PlayerContextValue = {
  episodeId: number | null;
  sourceUrl: string;
  title: string;
  duration: number;
  currentTime: number;
  playing: boolean;
  rate: number;
  error: boolean;
  load: (
    episodeId: number | null,
    title: string,
    duration: number,
    sourceUrl?: string
  ) => void;
  toggle: () => void;
  seek: (seconds: number) => void;
  skip: (seconds: number) => void;
  cycleRate: () => void;
};

const PlayerContext = createContext<PlayerContextValue | null>(null);

export function PlayerProvider({ children }: PropsWithChildren) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const rateRef = useRef(1);
  const [episodeId, setEpisodeId] = useState<number | null>(null);
  const [sourceUrl, setSourceUrl] = useState("");
  const [title, setTitle] = useState("");
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const [error, setError] = useState(false);

  const load = useCallback(
    (
      nextId: number | null,
      nextTitle: string,
      nextDuration: number,
      requestedSourceUrl?: string
    ) => {
      const nextSourceUrl =
        requestedSourceUrl ??
        (nextId === null ? "" : `/api/v1/episodes/${nextId}/preview`);
      setTitle(nextTitle);
      setError(false);
      if (
        episodeId === nextId &&
        title === nextTitle &&
        sourceUrl === nextSourceUrl
      ) {
        if (nextId === null) {
          setPlaying((value) => !value);
          return;
        }
        if (audio.current?.paused) {
          void audio.current.play().catch(() => setError(true));
        } else {
          audio.current?.pause();
        }
        return;
      }
      setEpisodeId(nextId);
      setSourceUrl(nextSourceUrl);
      setDuration(nextDuration);
      setCurrentTime(0);
      if (audio.current && nextId !== null) {
        audio.current.src = nextSourceUrl;
        // The playback rate resets with a new source; reapply the
        // owner's chosen rate so it persists across episodes.
        audio.current.playbackRate = rateRef.current;
        void audio.current.play().catch(() => setError(true));
      } else {
        setPlaying(nextId === null);
      }
    },
    [episodeId, sourceUrl, title]
  );

  const toggle = useCallback(() => {
    if (!audio.current || episodeId === null) {
      setPlaying((value) => !value);
      return;
    }
    if (audio.current.paused) {
      void audio.current.play().catch(() => setError(true));
    } else {
      audio.current.pause();
    }
  }, [episodeId]);

  const seek = useCallback(
    (seconds: number) => {
      const limit = duration > 0 ? duration : Number.MAX_SAFE_INTEGER;
      const next = Math.min(Math.max(0, seconds), limit);
      if (audio.current && episodeId !== null) {
        audio.current.currentTime = next;
      }
      setCurrentTime(next);
    },
    [duration, episodeId]
  );

  const skip = useCallback(
    (seconds: number) => {
      // Read the element's clock directly so rapid taps accumulate
      // instead of racing a stale state snapshot.
      const base =
        audio.current && episodeId !== null
          ? audio.current.currentTime
          : currentTime;
      seek(base + seconds);
    },
    [currentTime, episodeId, seek]
  );

  const cycleRate = useCallback(() => {
    const index = PLAYBACK_RATES.indexOf(rateRef.current);
    const next =
      PLAYBACK_RATES[(index + 1) % PLAYBACK_RATES.length] ?? 1;
    rateRef.current = next;
    setRate(next);
    if (audio.current) {
      audio.current.playbackRate = next;
    }
  }, []);

  return (
    <PlayerContext.Provider
      value={{
        episodeId,
        sourceUrl,
        title,
        duration,
        currentTime,
        playing,
        rate,
        error,
        load,
        toggle,
        seek,
        skip,
        cycleRate
      }}
    >
      {children}
      <audio
        ref={audio}
        preload="metadata"
        onEnded={() => setPlaying(false)}
        onPause={() => setPlaying(false)}
        onPlay={() => {
          setPlaying(true);
          setError(false);
        }}
        onError={() => {
          setPlaying(false);
          setError(true);
        }}
        onDurationChange={(event) => {
          const measured = event.currentTarget.duration;
          if (Number.isFinite(measured) && measured > 0) {
            setDuration(measured);
          }
        }}
        onLoadedMetadata={(event) => {
          event.currentTarget.playbackRate = rateRef.current;
        }}
        onTimeUpdate={(event) =>
          setCurrentTime(event.currentTarget.currentTime)
        }
      />
    </PlayerContext.Provider>
  );
}

export function usePlayer(): PlayerContextValue {
  const value = useContext(PlayerContext);
  if (!value) {
    throw new Error("usePlayer must be used inside PlayerProvider");
  }
  return value;
}

export function formatPlayerTime(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const remaining = safe % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`
    : `${minutes}:${String(remaining).padStart(2, "0")}`;
}
