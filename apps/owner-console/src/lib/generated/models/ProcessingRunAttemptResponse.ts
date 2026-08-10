/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ProcessingRunAttemptResponse = {
  completed_at: (string | null);
  correlation_id: string;
  created_at: string;
  duration_ms: number;
  error_kind: (string | null);
  error_summary: (string | null);
  id: number;
  is_current_blocker: boolean;
  resume_checkpoint: string;
  routing: Record<string, any>;
  sequence: number;
  stage: string;
  started_at: string;
  status: string;
  superseded: boolean;
  supersedes_attempt_id: (number | null);
  timing_provenance: string;
  transfer_bytes: number;
};
