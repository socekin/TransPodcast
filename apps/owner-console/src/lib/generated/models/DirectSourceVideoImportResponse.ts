/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ImportedSourceChannelResponse } from './ImportedSourceChannelResponse';
export type DirectSourceVideoImportResponse = {
  candidate_status: string;
  candidate_video_id: number;
  episode_id: (number | null);
  processing_run_id: (number | null);
  reused: boolean;
  run_status: (string | null);
  source_channel: ImportedSourceChannelResponse;
  youtube_video_id: string;
};
