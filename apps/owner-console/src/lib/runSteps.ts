import { copy } from "../i18n/zh-CN";
import {
  asListeningAudit,
  type ApiListeningAudit,
  type ApiPipelineStep,
  type ApiRun
} from "./api";
import { formatElapsed, formatLength, parseInstant } from "./format";

export type StepTone = "done" | "current" | "blocked" | "todo" | "skipped" | "closed";

export function stepTone(step: ApiPipelineStep): StepTone {
  switch (step.state) {
    case "completed":
      return "done";
    case "not_applicable":
      return "skipped";
    case "not_started":
    case "queued":
      return "todo";
    case "active":
    case "waiting_to_retry":
      return "current";
    case "closed":
      return "closed";
    default:
      return "blocked";
  }
}

export function stepLabel(id: string): string {
  return copy.steps[id] ?? id;
}

export function agentName(provider: string | null | undefined): string {
  if (!provider) {
    return "";
  }
  if (provider.startsWith("claude")) {
    return "Claude Code";
  }
  if (provider.startsWith("codex")) {
    return "Codex";
  }
  return provider;
}

export function voiceName(model: unknown): string {
  const text = Array.isArray(model) ? model.join(" ") : String(model ?? "");
  if (/2\.5|indextts25/i.test(text)) {
    return "IndexTTS 2.5";
  }
  if (/indextts/i.test(text)) {
    return "IndexTTS 2.0";
  }
  return "";
}

function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function stepAudit(step: ApiPipelineStep | undefined): ApiListeningAudit | null {
  return step ? asListeningAudit(step.metrics?.listening_audit) : null;
}

/** The short note at the end of a step row. */
export function stepNote(step: ApiPipelineStep, run: ApiRun): string {
  const tone = stepTone(step);
  if (tone === "skipped") {
    return copy.stepNotes.skipped;
  }
  if (tone === "closed") {
    return copy.stepNotes.closed;
  }
  if (tone === "blocked") {
    return run.status === "review_queue"
      ? copy.stepNotes.waiting
      : step.state === "paused" || step.state === "spend_guardrail"
        ? copy.stepNotes.paused
        : copy.stepNotes.failed;
  }
  if (step.state === "waiting_to_retry") {
    return copy.stepNotes.retrying;
  }
  if (tone === "current" && step.progress) {
    return copy.stepNotes.lines(step.progress.completed, step.progress.total);
  }
  if (tone !== "done") {
    return "";
  }
  const speakers = number(step.metrics?.speakers);
  if (step.id === "source_analysis" && speakers !== null) {
    return copy.stepNotes.speakers(speakers);
  }
  if (step.id === "translation_authoring") {
    return agentName(run.translation.provider) || copy.stepNotes.done;
  }
  if (step.id === "voice_render") {
    return voiceName(step.technical_evidence?.model) || copy.stepNotes.done;
  }
  if (step.duration_ms !== null && step.duration_ms >= 60000) {
    return formatElapsed(step.duration_ms);
  }
  return copy.stepNotes.done;
}

/** Plain-language lines shown when a step is opened. */
export function stepDetails(
  step: ApiPipelineStep,
  run: ApiRun,
  sourceSeconds: number
): string[] {
  const tone = stepTone(step);
  const details = copy.stepDetails;
  if (tone === "skipped") {
    return [details.skipped];
  }
  if (tone === "todo") {
    return [details.notStarted];
  }
  const lines: string[] = [];
  const metrics = step.metrics ?? {};
  const cost = number(metrics.estimated_cost_usd);
  switch (step.id) {
    case "source_archive":
      if (tone === "done" && sourceSeconds > 0) {
        lines.push(details.archived(formatLength(sourceSeconds)));
      }
      break;
    case "source_analysis": {
      const speakers = number(metrics.speakers);
      const segments = number(metrics.segments);
      if (speakers !== null && segments !== null) {
        lines.push(details.analysis(speakers, segments));
      }
      break;
    }
    case "translation_authoring":
    case "translation_review": {
      const progress = step.progress;
      if (progress && tone === "current") {
        lines.push(details.translating(progress.completed, progress.total));
      } else if (progress) {
        lines.push(
          step.id === "translation_authoring"
            ? details.translated(progress.total)
            : details.reviewed(progress.total)
        );
      }
      const project = run.translation_project;
      const turns =
        step.id === "translation_authoring"
          ? project?.author_turns
          : project?.reviewer_turns;
      const name = agentName(run.translation.provider);
      if (name && turns) {
        lines.push(details.agentTurns(name, turns));
      }
      break;
    }
    case "content_editing":
      if (tone === "done") {
        lines.push(details.contentEdited);
      }
      break;
    case "voice_render": {
      const rendered = number(metrics.rendered_segments);
      if (rendered !== null) {
        lines.push(details.rendered(rendered));
      }
      break;
    }
    case "publication_audio_quality": {
      const lufs = number(metrics.loudness_lufs);
      const peak = number(metrics.true_peak_dbtp);
      if (lufs !== null && peak !== null) {
        lines.push(details.loudness(lufs, peak));
      }
      const audit = stepAudit(step);
      if (audit) {
        lines.push(details.audit(audit.hard_count, audit.soft_count));
      }
      break;
    }
    case "publication_queue":
      lines.push(details.queue);
      break;
  }
  const timing: string[] = [];
  if (step.duration_ms !== null && step.duration_ms > 0 && tone === "done") {
    timing.push(details.duration(formatElapsed(step.duration_ms)));
  }
  if (cost !== null && cost > 0) {
    timing.push(details.modalCost(cost.toFixed(2)));
  }
  if (timing.length) {
    lines.push(timing.join(" · "));
  }
  return lines.length ? lines : [copy.stepNotes.done];
}

/** Steps that count towards progress: finished or not needed. */
export function finishedSteps(run: ApiRun): number {
  return run.pipeline_steps.filter((step) =>
    ["completed", "not_applicable"].includes(step.state)
  ).length;
}

/** The step the run is on: the first one neither finished nor skipped. */
export function currentStep(run: ApiRun): ApiPipelineStep | undefined {
  return run.pipeline_steps.find(
    (step) => !["done", "skipped"].includes(stepTone(step))
  );
}

/** Wall time since the run was first picked up. */
export function runElapsedMs(run: ApiRun, now: number): number | null {
  const start = parseInstant(run.first_claimed_at ?? run.started_at);
  if (start === null) {
    return null;
  }
  const end = parseInstant(run.completed_at) ?? now;
  return Math.max(0, end - start);
}
