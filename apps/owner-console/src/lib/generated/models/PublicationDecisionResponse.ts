/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type PublicationDecisionResponse = {
  already_applied: boolean;
  channel: PublicationDecisionResponse.channel;
  decision: PublicationDecisionResponse.decision;
  episode_id: number;
  state: string;
};
export namespace PublicationDecisionResponse {
  export enum channel {
    OWNER_CONSOLE = 'owner_console',
    TELEGRAM = 'telegram',
  }
  export enum decision {
    PUBLISH = 'publish',
    DO_NOT_PUBLISH = 'do_not_publish',
  }
}
