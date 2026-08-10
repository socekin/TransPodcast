/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChannelSpeakerProfileState } from './ChannelSpeakerProfileState';
import type { VoiceReferencePreviewResponse } from './VoiceReferencePreviewResponse';
export type ChannelSpeakerProfileResponse = {
  display_name: string;
  id: number;
  speaker_id: number;
  state: ChannelSpeakerProfileState;
  voice_reference: VoiceReferencePreviewResponse;
};
