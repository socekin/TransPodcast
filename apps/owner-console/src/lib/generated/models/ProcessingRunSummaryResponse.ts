/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProcessingRunSummaryTranslationResponse } from './ProcessingRunSummaryTranslationResponse';
export type ProcessingRunSummaryResponse = {
  current_step: string;
  estimated_cost_usd: number;
  id: number;
  latest_change_at: string;
  owner_state: string;
  revision: string;
  source_show: string;
  status: string;
  title: string;
  translation: ProcessingRunSummaryTranslationResponse;
};
