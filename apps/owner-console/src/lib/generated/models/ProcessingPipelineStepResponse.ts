/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PipelineStepProgressResponse } from './PipelineStepProgressResponse';
import type { PipelineStepRecoveryResponse } from './PipelineStepRecoveryResponse';
import type { PipelineTechnicalEvidenceResponse } from './PipelineTechnicalEvidenceResponse';
export type ProcessingPipelineStepResponse = {
  completed_at: (string | null);
  duration_ms: (number | null);
  evidence_available: boolean;
  id: string;
  metrics: Record<string, any>;
  progress: (PipelineStepProgressResponse | null);
  recovery: PipelineStepRecoveryResponse;
  started_at: (string | null);
  state: string;
  summary: string;
  technical_evidence: (PipelineTechnicalEvidenceResponse | null);
};
