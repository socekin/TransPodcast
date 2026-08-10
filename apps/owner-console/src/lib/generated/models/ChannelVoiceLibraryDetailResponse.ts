/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChannelSpeakerProfileResponse } from './ChannelSpeakerProfileResponse';
import type { ChannelVoiceCandidateResponse } from './ChannelVoiceCandidateResponse';
import type { ChannelVoiceLibraryChannelResponse } from './ChannelVoiceLibraryChannelResponse';
export type ChannelVoiceLibraryDetailResponse = {
  channel: ChannelVoiceLibraryChannelResponse;
  pending_candidates: Array<ChannelVoiceCandidateResponse>;
  saved_profiles: Array<ChannelSpeakerProfileResponse>;
};
