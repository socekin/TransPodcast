import { Play } from "lucide-react";
import { copy } from "../i18n/zh-CN";
import type {
  ApiListeningAuditFinding,
  ApiListeningAuditSummary
} from "../lib/api";
import { Badge, Button } from "./ui";

const auditCopy = copy.listeningAudit;

function kindLabel(kind: string) {
  return auditCopy.kinds[kind as keyof typeof auditCopy.kinds] ?? kind;
}

function severityBadge(finding: ApiListeningAuditFinding) {
  if (finding.kind === "audit_unavailable") {
    return <Badge tone="neutral">{auditCopy.unavailable}</Badge>;
  }
  return finding.severity === "hard" ? (
    <Badge tone="danger">{auditCopy.hard}</Badge>
  ) : (
    <Badge tone="warning">{auditCopy.soft}</Badge>
  );
}

export function ListeningAuditFindingList({
  findings,
  onListen
}: {
  findings: ApiListeningAuditFinding[];
  onListen?: (seconds: number) => void;
}) {
  return (
    <ul className="listening-audit-findings">
      {findings.map((finding, index) => (
        <li key={`${finding.unit_id}-${finding.kind}-${index}`}>
          {severityBadge(finding)}
          <strong>{kindLabel(finding.kind)}</strong>
          <span>
            {finding.unit_id
              ? auditCopy.unitLabel(finding.unit_id)
              : auditCopy.episodeWide}
          </span>
          <small>{finding.detail}</small>
          {onListen && finding.output_start_seconds !== null ? (
            <Button
              tone="ghost"
              size="sm"
              onClick={() => onListen(finding.output_start_seconds ?? 0)}
            >
              <Play size={12} aria-hidden="true" />
              {auditCopy.listen}
            </Button>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export function ListeningAuditPanel({
  audit,
  onListen
}: {
  audit: ApiListeningAuditSummary;
  onListen?: (seconds: number) => void;
}) {
  const statusLabel =
    auditCopy.status[audit.status as keyof typeof auditCopy.status] ??
    audit.status;
  const blocking =
    audit.blocking_unit_ids.length > 0 && !audit.owner_acknowledged;
  return (
    <section
      className="listening-audit"
      data-blocking={blocking || undefined}
      aria-label={auditCopy.title}
    >
      <header>
        <strong>{auditCopy.title}</strong>
        <Badge
          tone={
            audit.hard_count > 0
              ? "danger"
              : audit.status === "complete"
                ? "success"
                : "warning"
          }
        >
          {statusLabel}
        </Badge>
        <span>
          {auditCopy.counts(
            audit.hard_count,
            audit.soft_count,
            audit.unavailable_count
          )}
        </span>
      </header>
      {audit.retry ? (
        <p>
          {audit.retry.status === "failed"
            ? auditCopy.retryFailed
            : auditCopy.retried(
                audit.retry.unit_ids?.length ?? 0,
                audit.retry.resolved_unit_ids?.length ?? 0
              )}
        </p>
      ) : null}
      {blocking ? <p>{auditCopy.blocking}</p> : null}
      {audit.owner_acknowledged ? <p>{auditCopy.acknowledged}</p> : null}
      {audit.unavailable_count > 0 ? <p>{auditCopy.unavailableNote}</p> : null}
      {audit.findings.length ? (
        <ListeningAuditFindingList
          findings={audit.findings}
          onListen={onListen}
        />
      ) : (
        <p>{auditCopy.clean}</p>
      )}
    </section>
  );
}
