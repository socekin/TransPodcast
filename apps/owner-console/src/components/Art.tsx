import {
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
  useLayoutEffect,
  useRef,
  useState
} from "react";

import { cn, DEFAULT_ARTWORK } from "../lib/utils";
import { BACKDROP_PATH, RING_PATH, SCALLOP_PATH } from "./shapes";

/** Rendered once; every artwork clips to this scallop. */
export function ShapeDefs() {
  return (
    <svg width="0" height="0" aria-hidden="true" className="shape-defs">
      <defs>
        <clipPath id="sc-scallop" clipPathUnits="objectBoundingBox">
          <path d={SCALLOP_PATH} />
        </clipPath>
      </defs>
    </svg>
  );
}

export function Backdrop({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 100 100"
      className={cn("backdrop-shape", className)}
    >
      <path d={BACKDROP_PATH} />
    </svg>
  );
}

export function Artwork({
  src,
  alt = "",
  className
}: {
  src: string | null | undefined;
  alt?: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  return (
    <img
      className={cn("artwork", className)}
      src={!src || failed ? DEFAULT_ARTWORK : src}
      alt={alt}
      onError={() => setFailed(true)}
      loading="lazy"
    />
  );
}

/** A point on the ring at a fraction of the way round, clockwise from the
 * top, in the ring's 0-100 coordinates. */
function ringPoint(path: SVGPathElement | null, fraction: number) {
  const clamped = Math.min(Math.max(fraction, 0), 1);
  if (path && typeof path.getTotalLength === "function") {
    try {
      const point = path.getPointAtLength(path.getTotalLength() * clamped);
      return { x: point.x, y: point.y };
    } catch {
      // jsdom has no geometry; fall through to the circle.
    }
  }
  const angle = clamped * Math.PI * 2;
  return { x: 50 + 48.5 * Math.sin(angle), y: 50 - 48.5 * Math.cos(angle) };
}

/** Large artwork inside the ring. With `onSeek`, the ring is a timeline:
 * drag around it, or use the arrow keys. */
export function RingArt({
  src,
  progress,
  onSeek,
  label,
  valueText,
  children
}: {
  src?: string | null;
  progress: number | null;
  onSeek?: (fraction: number) => void;
  label?: string;
  valueText?: string;
  children?: ReactNode;
}) {
  const pathRef = useRef<SVGPathElement | null>(null);
  const frameRef = useRef<HTMLDivElement | null>(null);
  const [dot, setDot] = useState(() => ringPoint(null, progress ?? 0));
  const dragging = useRef(false);

  useLayoutEffect(() => {
    setDot(ringPoint(pathRef.current, progress ?? 0));
  }, [progress]);

  const fractionAt = (event: PointerEvent<HTMLDivElement>) => {
    const box = frameRef.current?.getBoundingClientRect();
    if (!box) {
      return null;
    }
    const x = event.clientX - (box.left + box.width / 2);
    const y = event.clientY - (box.top + box.height / 2);
    const angle = Math.atan2(x, -y);
    return (angle < 0 ? angle + Math.PI * 2 : angle) / (Math.PI * 2);
  };

  const interactive = Boolean(onSeek);
  const handleKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!onSeek || progress === null) {
      return;
    }
    const step = event.shiftKey ? 0.05 : 0.01;
    if (event.key === "ArrowRight" || event.key === "ArrowUp") {
      event.preventDefault();
      onSeek(Math.min(1, progress + step));
    } else if (event.key === "ArrowLeft" || event.key === "ArrowDown") {
      event.preventDefault();
      onSeek(Math.max(0, progress - step));
    }
  };

  return (
    <div
      ref={frameRef}
      className={cn("ring-art", interactive && "is-seekable")}
      role={interactive ? "slider" : undefined}
      tabIndex={interactive ? 0 : undefined}
      aria-label={interactive ? label : undefined}
      aria-valuemin={interactive ? 0 : undefined}
      aria-valuemax={interactive ? 100 : undefined}
      aria-valuenow={
        interactive ? Math.round((progress ?? 0) * 100) : undefined
      }
      aria-valuetext={interactive ? valueText : undefined}
      onKeyDown={handleKey}
      onPointerDown={(event) => {
        if (!onSeek) {
          return;
        }
        const box = frameRef.current?.getBoundingClientRect();
        if (!box) {
          return;
        }
        // Only the ring band seeks; the artwork centre stays a plain image.
        const dx = event.clientX - (box.left + box.width / 2);
        const dy = event.clientY - (box.top + box.height / 2);
        if (Math.hypot(dx, dy) < box.width * 0.38) {
          return;
        }
        dragging.current = true;
        event.currentTarget.setPointerCapture?.(event.pointerId);
        const fraction = fractionAt(event);
        if (fraction !== null) {
          onSeek(fraction);
        }
      }}
      onPointerMove={(event) => {
        if (!dragging.current || !onSeek) {
          return;
        }
        const fraction = fractionAt(event);
        if (fraction !== null) {
          onSeek(fraction);
        }
      }}
      onPointerUp={() => {
        dragging.current = false;
      }}
      onPointerCancel={() => {
        dragging.current = false;
      }}
    >
      <svg className="ring-art-line" viewBox="0 0 100 100" aria-hidden="true">
        <path ref={pathRef} d={RING_PATH} />
      </svg>
      {progress !== null ? (
        <span
          className="ring-art-dot"
          aria-hidden="true"
          style={{ left: `${dot.x}%`, top: `${dot.y}%` }}
        />
      ) : null}
      {children ?? <Artwork className="ring-art-image" src={src} />}
    </div>
  );
}

/** Processing progress drawn along the same ring. */
export function ProgressRing({
  fraction,
  label,
  children
}: {
  fraction: number;
  label: string;
  children: ReactNode;
}) {
  const value = Math.round(Math.min(Math.max(fraction, 0), 1) * 100);
  return (
    <div className="ring-art progress-ring" role="img" aria-label={label}>
      <svg className="ring-art-line" viewBox="0 0 100 100" aria-hidden="true">
        <path d={RING_PATH} />
        <path
          className="progress-ring-value"
          d={RING_PATH}
          pathLength={100}
          strokeDasharray={`${value} 100`}
        />
      </svg>
      <div className="progress-ring-center">{children}</div>
    </div>
  );
}
