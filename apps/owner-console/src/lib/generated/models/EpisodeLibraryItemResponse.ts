/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EpisodeLibraryAction } from './EpisodeLibraryAction';
import type { EpisodeLibraryState } from './EpisodeLibraryState';
export type EpisodeLibraryItemResponse = {
  artwork_url: string;
  available_actions: Array<EpisodeLibraryAction>;
  can_reprocess: boolean;
  created_at: string;
  duration_seconds: number;
  episode_id: (number | null);
  guests: Array<string>;
  library_state: EpisodeLibraryState;
  overview: (string | null);
  processing_run_id: number;
  published_at: (string | null);
  source_published_at: string;
  source_show: string;
  source_title: string;
  source_url: string;
  stage: (string | null);
  state: string;
  title: string;
};
