import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { App } from "../App";
import { copy } from "../i18n/zh-CN";

afterEach(() => {
  vi.unstubAllGlobals();
});

it("maintains a reusable Speaker Profile for the selected channel", async () => {
  let saved = false;
  let active = false;
  let ignored = false;
  const voiceReference = {
    id: 11,
    duration_seconds: 12,
    audio_url: "/api/v1/speaker-voice-references/11/audio"
  };
  const savedProfile = {
    id: 21,
    speaker_id: 31,
    display_name: "Jordan Vale",
    state: "active",
    voice_reference: voiceReference
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/channel-voice-library") {
        return Response.json({
          items: [
            {
              source_channel_id: 1,
              title: "Founder Roundtable",
              avatar_url: "",
              saved_profile_count: active ? 1 : 0,
          pending_candidate_count: saved || ignored ? 0 : 1
            },
            {
              source_channel_id: 2,
              title: "Signal Foundry",
              avatar_url: "",
              saved_profile_count: 0,
              pending_candidate_count: 4
            }
          ]
        });
      }
      if (url === "/api/v1/source-channels/1/voice-library") {
        return Response.json({
          channel: {
            id: 1,
            title: "Founder Roundtable",
            avatar_url: ""
          },
          saved_profiles: active ? [savedProfile] : [],
          pending_candidates: saved || ignored
            ? []
            : [
                {
                  id: 41,
                  speaker_id: 31,
                  display_name: "speaker_0 · sample episode",
                  local_label: "speaker_0",
                  segment_count: 149,
                  source_episode: {
                    id: 51,
                    title: "Responsible AI in Practice"
                  },
                  voice_reference: voiceReference
                }
              ]
        });
      }
      if (
        url === "/api/v1/channel-voice-candidates/41:ignore" &&
        init?.method === "POST"
      ) {
        ignored = true;
        return Response.json({ id: 41, decision: "ignored" });
      }
      if (
        url === "/api/v1/channel-voice-candidates/41:restore" &&
        init?.method === "POST"
      ) {
        ignored = false;
        return Response.json({ id: 41, decision: "pending" });
      }
      if (
        url === "/api/v1/channel-voice-candidates/41:save" &&
        init?.method === "POST"
      ) {
        saved = true;
        active = true;
        return Response.json(savedProfile);
      }
      if (
        url === "/api/v1/channel-speaker-profiles/21:stop-reuse" &&
        init?.method === "POST"
      ) {
        active = false;
        return Response.json({ ...savedProfile, state: "inactive" });
      }
      throw new Error(`Unexpected request: ${url}`);
    })
  );
  const user = userEvent.setup();

  render(<App initialView="channelVoices" />);

  expect(
    await screen.findByRole("heading", {
      name: copy.channelVoices.title
    })
  ).toBeInTheDocument();
  expect(
    screen.getByRole("searchbox", {
      name: copy.channelVoices.searchChannels
    })
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: /Founder Roundtable/ })
    ).toHaveAttribute("aria-current", "true")
  );
  expect(
    await screen.findByText(copy.channelVoices.pendingTitle)
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", {
      name: copy.channelVoices.listenCandidate
    })
  ).toBeInTheDocument();

  await user.click(
    screen.getByRole("button", { name: copy.channelVoices.ignore })
  );
  expect(
    await screen.findByText(copy.channelVoices.ignoredNotice)
  ).toBeInTheDocument();
  await user.click(
    screen.getByRole("button", { name: copy.channelVoices.undo })
  );
  expect(
    await screen.findByRole("button", {
      name: copy.channelVoices.listenCandidate
    })
  ).toBeInTheDocument();

  await user.type(
    screen.getByPlaceholderText(copy.channelVoices.namePlaceholder),
    "Jordan Vale"
  );
  await user.click(
    screen.getByRole("button", { name: copy.channelVoices.saveReuse })
  );

  expect(await screen.findByText("Jordan Vale")).toBeInTheDocument();
  expect(
    screen.getByRole("button", {
      name: copy.channelVoices.listenCanonical
    })
  ).toBeInTheDocument();

  await user.click(
    screen.getByRole("button", { name: copy.channelVoices.stopReuse })
  );
  expect(
    screen.getByText(copy.channelVoices.stopReuseExplanation)
  ).toBeInTheDocument();
  await user.click(
    screen.getByRole("button", {
      name: copy.channelVoices.confirmStopReuse
    })
  );

  expect(
    await screen.findByText(copy.channelVoices.noSavedTitle)
  ).toBeInTheDocument();
});

it("merges a duplicate candidate into a saved Speaker Profile", async () => {
  let merged = false;
  const currentReference = {
    id: 11,
    duration_seconds: 12,
    audio_url: "/api/v1/speaker-voice-references/11/audio"
  };
  const replacementReference = {
    id: 12,
    duration_seconds: 12,
    audio_url: "/api/v1/speaker-voice-references/12/audio"
  };
  const savedProfile = {
    id: 21,
    speaker_id: 31,
    display_name: "Known speaker",
    state: "active",
    voice_reference: merged ? replacementReference : currentReference
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/channel-voice-library") {
        return Response.json({
          items: [
            {
              source_channel_id: 1,
              title: "Founder Roundtable",
              avatar_url: "",
              saved_profile_count: 1,
              pending_candidate_count: merged ? 0 : 1
            }
          ]
        });
      }
      if (url === "/api/v1/source-channels/1/voice-library") {
        return Response.json({
          channel: {
            id: 1,
            title: "Founder Roundtable",
            avatar_url: ""
          },
          saved_profiles: [{ ...savedProfile }],
          pending_candidates: merged
            ? []
            : [
                {
                  id: 41,
                  speaker_id: 32,
                  display_name: "speaker_1 · sample episode",
                  local_label: "speaker_1",
                  segment_count: 148,
                  source_episode: {
                    id: 51,
                    title: "Responsible AI in Practice"
                  },
                  voice_reference: replacementReference
                }
              ]
        });
      }
      if (
        url === "/api/v1/channel-voice-candidates/41:merge" &&
        init?.method === "POST"
      ) {
        expect(JSON.parse(String(init.body))).toEqual({
          target_profile_id: 21,
          replace_canonical_reference: true
        });
        merged = true;
        return Response.json({
          ...savedProfile,
          voice_reference: replacementReference
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    })
  );
  const user = userEvent.setup();

  render(<App initialView="channelVoices" />);

  await user.click(
    await screen.findByRole("button", {
      name: copy.channelVoices.mergeExisting
    })
  );
  await user.click(
    screen.getByRole("checkbox", {
      name: copy.channelVoices.replaceReference
    })
  );
  await user.click(
    screen.getByRole("button", {
      name: copy.channelVoices.confirmMerge
    })
  );

  expect(
    await screen.findByText(copy.channelVoices.noPendingTitle)
  ).toBeInTheDocument();
});

it("keeps a failed profile action scoped to that profile", async () => {
  const savedProfile = {
    id: 21,
    speaker_id: 31,
    display_name: "Known speaker",
    state: "active",
    voice_reference: {
      id: 11,
      duration_seconds: 12,
      audio_url: "/api/v1/speaker-voice-references/11/audio"
    }
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/channel-voice-library") {
        return Response.json({
          items: [
            {
              source_channel_id: 1,
              title: "Founder Roundtable",
              avatar_url: "",
              saved_profile_count: 1,
              pending_candidate_count: 0
            }
          ]
        });
      }
      if (url === "/api/v1/source-channels/1/voice-library") {
        return Response.json({
          channel: {
            id: 1,
            title: "Founder Roundtable",
            avatar_url: ""
          },
          saved_profiles: [savedProfile],
          pending_candidates: []
        });
      }
      if (
        url === "/api/v1/channel-speaker-profiles/21:stop-reuse" &&
        init?.method === "POST"
      ) {
        return Response.json(
          { detail: "Temporary database failure" },
          { status: 500 }
        );
      }
      throw new Error(`Unexpected request: ${url}`);
    })
  );
  const user = userEvent.setup();

  render(<App initialView="channelVoices" />);

  await user.click(
    await screen.findByRole("button", {
      name: copy.channelVoices.stopReuse
    })
  );
  await user.click(
    screen.getByRole("button", {
      name: copy.channelVoices.confirmStopReuse
    })
  );

  expect(
    await screen.findByRole("alert")
  ).toHaveTextContent(copy.channelVoices.operationFailed);
  expect(
    screen.getByRole("button", {
      name: copy.channelVoices.confirmStopReuse
    })
  ).toBeInTheDocument();
});
