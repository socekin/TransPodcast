/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChannelSpeakerSuggestionResponse } from './ChannelSpeakerSuggestionResponse';
import type { ChannelVoiceCandidateEpisodeResponse } from './ChannelVoiceCandidateEpisodeResponse';
import type { VoiceReferencePreviewResponse } from './VoiceReferencePreviewResponse';
export type ChannelVoiceCandidateResponse = {
  display_name: string;
  id: number;
  local_label: string;
  possible_matches: Array<ChannelSpeakerSuggestionResponse>;
  segment_count: number;
  source_episode: ChannelVoiceCandidateEpisodeResponse;
  speaker_id: number;
  voice_reference: VoiceReferencePreviewResponse;
};
