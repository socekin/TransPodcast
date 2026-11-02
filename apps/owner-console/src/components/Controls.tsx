import {
  type ReactNode,
  type RefObject,
  useEffect,
  useId,
  useRef,
  useState
} from "react";

import { copy } from "../i18n/zh-CN";
import { api } from "../lib/api";
import { formatRate } from "../lib/format";
import { PLAYBACK_RATES } from "../lib/player";
import { followLink, type Route, useNavigate, pathForRoute } from "../lib/routing";
import { cn } from "../lib/utils";
import { BackIcon, GaugeIcon, MoreIcon } from "./Icons";

/** Close a popover on an outside press or Escape. */
function useDismiss(
  open: boolean,
  close: () => void,
  container: RefObject<HTMLElement | null>
) {
  useEffect(() => {
    if (!open) {
      return;
    }
    const onPointer = (event: PointerEvent) => {
      if (!container.current?.contains(event.target as Node)) {
        close();
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        close();
      }
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close, container]);
}

/** Speed pill that opens the full set of rates. Used by every large
 * player; `note` says whether the choice applies everywhere or to one
 * line. */
export function SpeedMenu({
  rate,
  onChange,
  note
}: {
  rate: number;
  onChange: (rate: number) => void;
  note: string;
}) {
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement | null>(null);
  const menuId = useId();
  useDismiss(open, () => setOpen(false), container);
  return (
    <div className="speed-menu" ref={container}>
      <button
        type="button"
        className={cn("speed-pill", open && "is-open")}
        aria-label={copy.speed.label(formatRate(rate))}
        aria-expanded={open}
        aria-controls={menuId}
        onClick={() => setOpen((value) => !value)}
      >
        {formatRate(rate)}
      </button>
      {open ? (
        <div
          className="speed-popover"
          id={menuId}
          role="group"
          aria-label={copy.speed.group}
        >
          <span>{note}</span>
          <div className="speed-options">
            {PLAYBACK_RATES.map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={value === rate}
                className={cn("speed-option", value === rate && "is-selected")}
                onClick={() => {
                  onChange(value);
                  setOpen(false);
                }}
              >
                {formatRate(value)}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** Speed control under the episode player's play button. The pill opens in
 * place into a row of rates, so choosing a speed covers nothing. */
export function SpeedBar({
  rate,
  onChange
}: {
  rate: number;
  onChange: (rate: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement | null>(null);
  const pill = useRef<HTMLButtonElement | null>(null);
  const selected = useRef<HTMLButtonElement | null>(null);
  const opened = useRef(false);
  useDismiss(open, () => setOpen(false), container);

  useEffect(() => {
    if (open) {
      opened.current = true;
      selected.current?.focus();
    } else if (opened.current && document.activeElement === document.body) {
      // The focused rate unmounted with the row; hand focus back to the pill.
      pill.current?.focus();
    }
  }, [open]);

  return (
    <div className="speed-bar" ref={container}>
      {open ? (
        <div
          className="speed-bar-rates"
          role="group"
          aria-label={copy.speed.global}
          title={copy.speed.global}
        >
          <button
            type="button"
            className="speed-bar-close"
            aria-label={copy.speed.collapse}
            onClick={() => setOpen(false)}
          >
            <GaugeIcon size={17} />
          </button>
          <div className="speed-bar-options">
            {PLAYBACK_RATES.map((value) => (
              <button
                key={value}
                ref={value === rate ? selected : undefined}
                type="button"
                aria-pressed={value === rate}
                className={cn("speed-bar-option", value === rate && "is-selected")}
                onClick={() => {
                  onChange(value);
                  setOpen(false);
                }}
              >
                {formatRate(value)}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <button
          ref={pill}
          type="button"
          className="speed-bar-pill"
          aria-label={copy.speed.label(formatRate(rate))}
          aria-expanded={false}
          onClick={() => setOpen(true)}
        >
          <GaugeIcon size={17} />
          {formatRate(rate)}
        </button>
      )}
    </div>
  );
}

/** Compact speed pill for the mini player: each press steps to the next
 * rate. */
export function SpeedCycle({
  rate,
  onCycle
}: {
  rate: number;
  onCycle: () => void;
}) {
  return (
    <button
      type="button"
      className="speed-pill is-dark"
      aria-label={copy.speed.cycleLabel(formatRate(rate))}
      onClick={onCycle}
    >
      {formatRate(rate)}
    </button>
  );
}

export function RouteLink({
  to,
  className,
  children,
  ...rest
}: {
  to: Route;
  className?: string;
  children: ReactNode;
  "aria-label"?: string;
}) {
  const navigate = useNavigate();
  return (
    <a
      href={pathForRoute(to)}
      className={className}
      onClick={(event) => followLink(event, navigate, to)}
      {...rest}
    >
      {children}
    </a>
  );
}

export function PageHeader({
  title,
  right
}: {
  title: string;
  right?: ReactNode;
}) {
  return (
    <header className="page-header">
      <RouteLink
        to={{ name: "home" }}
        className="circle-button is-large"
        aria-label={copy.actions.back}
      >
        <BackIcon />
      </RouteLink>
      <h1>{title}</h1>
      <div className="page-header-end">{right}</div>
    </header>
  );
}

export type MenuItem = {
  label: string;
  onSelect: () => void;
  tone?: "danger";
};

export function MoreMenu({ items }: { items: MenuItem[] }) {
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement | null>(null);
  useDismiss(open, () => setOpen(false), container);
  if (items.length === 0) {
    return null;
  }
  return (
    <div className="more-menu" ref={container}>
      <button
        type="button"
        className="circle-button is-large"
        aria-label={copy.actions.more}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <MoreIcon />
      </button>
      {open ? (
        <div className="more-menu-list" role="menu">
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              className={cn(item.tone === "danger" && "is-danger")}
              onClick={() => {
                setOpen(false);
                item.onSelect();
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function DiagnosticsButton({ runId }: { runId: number }) {
  const [state, setState] = useState<"idle" | "copying" | "copied" | "error">(
    "idle"
  );
  return (
    <button
      type="button"
      className="text-button"
      disabled={state === "copying"}
      onClick={() => {
        setState("copying");
        api
          .diagnostics(runId)
          .then((value) =>
            navigator.clipboard.writeText(JSON.stringify(value, null, 2))
          )
          .then(() => setState("copied"))
          .catch(() => setState("error"));
      }}
    >
      {state === "copied"
        ? copy.actions.diagnosticsCopied
        : state === "error"
          ? copy.actions.diagnosticsFailed
          : state === "copying"
            ? copy.actions.working
            : copy.actions.copyDiagnostics}
    </button>
  );
}

/** Light / bold heading pair used across the console. */
export function MixedHeading({
  bold,
  light,
  boldFirst = true,
  as: Tag = "h2",
  className,
  id
}: {
  bold: string;
  light: string;
  boldFirst?: boolean;
  as?: "h1" | "h2" | "p";
  className?: string;
  id?: string;
}) {
  return (
    <Tag className={cn("mixed-heading", className)} id={id}>
      {boldFirst ? (
        <>
          <b>{bold}</b>
          {light}
        </>
      ) : (
        <>
          {light}
          <b>{bold}</b>
        </>
      )}
    </Tag>
  );
}
