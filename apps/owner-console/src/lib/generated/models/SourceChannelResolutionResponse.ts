/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ResolvedSourceChannel } from './ResolvedSourceChannel';
import type { SourceVideoSummary } from './SourceVideoSummary';
export type SourceChannelResolutionResponse = {
  channel: ResolvedSourceChannel;
  recent_videos: Array<SourceVideoSummary>;
  supported_source_video: (SourceVideoSummary | null);
};
