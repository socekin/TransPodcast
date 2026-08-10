/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ResolvedSourceResponseChannel } from './ResolvedSourceResponseChannel';
import type { ResolvedSourceVideo } from './ResolvedSourceVideo';
import type { SourceChannelResponse } from './SourceChannelResponse';
export type SourceResolutionResponse = {
  channel: ResolvedSourceResponseChannel;
  existing_source_channel: (SourceChannelResponse | null);
  expires_at: string;
  requested_video: (ResolvedSourceVideo | null);
  resolution_id: string;
  timings_ms: Record<string, number>;
};
