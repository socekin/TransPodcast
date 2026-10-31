import { type ReactNode, useEffect, useRef, useState } from "react";

import type { ApiRun } from "../lib/api";
import {
  stepDetails,
  stepLabel,
  stepNote,
  stepTone
} from "../lib/runSteps";
import { cn } from "../lib/utils";
import { CheckIcon, ChevronDownIcon } from "./Icons";

/** Expandable pipeline steps. A blocked step opens on its own and shows
 * `decision` instead of the generic detail lines. */
export function StepList({
  run,
  sourceSeconds,
  decision
}: {
  run: ApiRun;
  sourceSeconds: number;
  decision?: ReactNode;
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const list = useRef<HTMLOListElement | null>(null);
  const hasAttention = run.pipeline_steps.some((step) =>
    ["blocked", "closed"].includes(stepTone(step))
  );

  // The step that needs the Owner starts in view.
  useEffect(() => {
    const container = list.current;
    const step = container?.querySelector<HTMLElement>(
      ".step.is-blocked, .step.is-closed"
    );
    if (container && step) {
      container.scrollTop +=
        step.getBoundingClientRect().top - container.getBoundingClientRect().top;
    }
  }, [hasAttention]);

  return (
    <ol className="step-list" ref={list}>
      {run.pipeline_steps.map((step, index) => {
        const tone = stepTone(step);
        const attention = tone === "blocked" || tone === "closed";
        const expanded = open[step.id] ?? attention;
        const panelId = `step-${step.id}`;
        return (
          <li key={step.id} className={cn("step", `is-${tone}`)}>
            <button
              type="button"
              className="step-row"
              aria-expanded={expanded}
              aria-controls={panelId}
              onClick={() =>
                setOpen((current) => ({ ...current, [step.id]: !expanded }))
              }
            >
              <span className="step-badge" aria-hidden="true">
                {tone === "done" ? (
                  <CheckIcon size={14} />
                ) : attention ? (
                  "!"
                ) : (
                  index + 1
                )}
              </span>
              <span className="step-label">{stepLabel(step.id)}</span>
              <span className="step-note">{stepNote(step, run)}</span>
              <ChevronDownIcon
                className={cn("step-chevron", expanded && "is-open")}
                size={16}
              />
            </button>
            {expanded ? (
              <div className="step-panel" id={panelId}>
                {attention && decision ? (
                  decision
                ) : (
                  <div className="step-detail">
                    {stepDetails(step, run, sourceSeconds).map((line) => (
                      <p key={line}>{line}</p>
                    ))}
                  </div>
                )}
              </div>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
