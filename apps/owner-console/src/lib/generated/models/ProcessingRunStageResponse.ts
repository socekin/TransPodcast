/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ProcessingRunStageResponse = {
  completed_at: string;
  duration_ms: number;
  input_hash: string;
  model: (string | null);
  output_hash: string;
  provider: (string | null);
  stage: string;
  started_at: string;
  usage: Record<string, any>;
  version: string;
};
