/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ProcessingSegmentRuling = {
  action: ProcessingSegmentRuling.action;
  speaker_id?: (number | null);
};
export namespace ProcessingSegmentRuling {
  export enum action {
    KEEP = 'keep',
    OVERTURN = 'overturn',
  }
}
