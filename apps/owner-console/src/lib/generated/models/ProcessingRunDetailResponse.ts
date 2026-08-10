/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProcessingPipelineStepResponse } from './ProcessingPipelineStepResponse';
import type { ProcessingRunActivityResponse } from './ProcessingRunActivityResponse';
import type { ProcessingRunAttemptResponse } from './ProcessingRunAttemptResponse';
import type { ProcessingRunErrorResponse } from './ProcessingRunErrorResponse';
import type { ProcessingRunRetryPlanResponse } from './ProcessingRunRetryPlanResponse';
import type { ProcessingRunStageResponse } from './ProcessingRunStageResponse';
import type { ProcessingRunTranslationResponse } from './ProcessingRunTranslationResponse';
import type { TranslationProjectProgressResponse } from './TranslationProjectProgressResponse';
export type ProcessingRunDetailResponse = {
  activity: Array<ProcessingRunActivityResponse>;
  attempt_count: number;
  attempt_history: Array<ProcessingRunAttemptResponse>;
  attempts: Array<ProcessingRunAttemptResponse>;
  candidate_video_id: number;
  completed_at: (string | null);
  current_error: (ProcessingRunErrorResponse | null);
  current_step: string;
  error: (ProcessingRunErrorResponse | null);
  estimated_cost_usd: number;
  first_claimed_at: (string | null);
  id: number;
  latest_change_at: string;
  owner_state: string;
  pipeline_steps: Array<ProcessingPipelineStepResponse>;
  pipeline_version: string;
  priority: number;
  queued_at: string;
  retry_plan: ProcessingRunRetryPlanResponse;
  revision: string;
  source_show: string;
  source_url: string;
  stage: string;
  stages: Array<ProcessingRunStageResponse>;
  started_at: string;
  state: string;
  status: string;
  title: string;
  translation: ProcessingRunTranslationResponse;
  translation_project: (TranslationProjectProgressResponse | null);
};
