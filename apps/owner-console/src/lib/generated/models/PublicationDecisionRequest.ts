/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type PublicationDecisionRequest = {
  decision: PublicationDecisionRequest.decision;
  expected_state?: (string | null);
};
export namespace PublicationDecisionRequest {
  export enum decision {
    PUBLISH = 'publish',
    DO_NOT_PUBLISH = 'do_not_publish',
  }
}
