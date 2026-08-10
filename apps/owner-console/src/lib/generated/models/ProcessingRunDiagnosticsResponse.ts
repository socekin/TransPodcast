/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProcessingRunErrorResponse } from './ProcessingRunErrorResponse';
import type { ProcessingRunRetryPlanResponse } from './ProcessingRunRetryPlanResponse';
import type { ProcessingRunTranslationResponse } from './ProcessingRunTranslationResponse';
export type ProcessingRunDiagnosticsResponse = {
  attempts: Array<Record<string, any>>;
  current_error: (ProcessingRunErrorResponse | null);
  retry_plan: ProcessingRunRetryPlanResponse;
  run: Record<string, any>;
  schema_version: string;
  stages: Array<Record<string, any>>;
  translation: ProcessingRunTranslationResponse;
};
