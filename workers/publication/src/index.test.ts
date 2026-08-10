import { describe, expect, it } from "vitest";

import { hashToken, routeRequest, tokensMatch } from "./router";

const token = "test-feed-token-with-sufficient-entropy";

function object(
  body: string,
  contentType: string,
  range?: R2Range,
  totalSize?: number,
): R2ObjectBody {
  const bytes = new TextEncoder().encode(body);
  return {
    body: new Blob([bytes]).stream(),
    bodyUsed: false,
    arrayBuffer: async () => bytes.buffer,
    text: async () => body,
    json: async () => JSON.parse(body),
    blob: async () => new Blob([bytes]),
    key: "fixture",
    version: "1",
    size: totalSize ?? bytes.byteLength,
    etag: "fixture",
    httpEtag: '"fixture"',
    checksums: {},
    uploaded: new Date("2026-07-28T00:00:00Z"),
    httpMetadata: { contentType },
    customMetadata: {},
    range,
    writeHttpMetadata(headers: Headers) {
      headers.set("Content-Type", contentType);
    }
  } as R2ObjectBody;
}

function environment(tokenHash?: string): Env {
  const media = {
    async get(key: string, options?: R2GetOptions) {
      if (key === "configuration/feed-token.sha256") {
        return tokenHash ? object(tokenHash, "text/plain") : null;
      }
      if (key === "feed/current.xml") {
        return object("<rss />", "application/rss+xml");
      }
      if (key.endsWith("chapters.json")) {
        return object('{"version":"1.2.0"}', "application/json+chapters");
      }
      if (key.endsWith("audio.m4a")) {
        const requested =
          options && "range" in options && options.range instanceof Headers
            ? options.range.get("Range")
            : null;
        if (requested === "bytes=-4") {
          return object("udio", "audio/mp4", { suffix: 4 }, 10);
        }
        if (requested === "bytes=0-1") {
          return object(
            "fu",
            "audio/mp4",
            {
              offset: 0,
              length: 2,
              suffix: undefined
            } as unknown as R2Range,
            10
          );
        }
        return object("full-audio", "audio/mp4");
      }
      return null;
    },
    async head() {
      return null;
    }
  };
  return { FEED_TOKEN: token, MEDIA: media as unknown as R2Bucket };
}

describe("publication Worker", () => {
  it("serves a valid channel homepage at the RSS channel link", async () => {
    const response = await routeRequest(
      new Request("https://podcast.test/"),
      environment()
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("Content-Type")).toContain("text/html");
    expect(await response.text()).toContain("TransPodcast");

    const head = await routeRequest(
      new Request("https://podcast.test/", { method: "HEAD" }),
      environment()
    );
    expect(head.status).toBe(200);
    expect(head.headers.get("Content-Length")).toBeNull();
  });

  it("uses constant-time token authorization", async () => {
    expect(await tokensMatch(token, token)).toBe(true);
    expect(await tokensMatch("wrong", token)).toBe(false);
  });

  it("serves feed and chapter metadata only with the private token", async () => {
    const env = environment();
    const denied = await routeRequest(
      new Request("https://podcast.test/feed/wrong.xml"),
      env
    );
    expect(denied.status).toBe(404);

    const feed = await routeRequest(
      new Request(`https://podcast.test/feed/${token}.xml`),
      env
    );
    expect(feed.status).toBe(200);
    expect(feed.headers.get("Content-Type")).toContain("application/rss+xml");

    const chapters = await routeRequest(
      new Request(
        `https://podcast.test/media/${token}/episodes/guid/v1/chapters.json`
      ),
      env
    );
    expect(chapters.status).toBe(200);
    expect(chapters.headers.get("Content-Type")).toBe(
      "application/json+chapters"
    );
    expect(chapters.headers.get("Last-Modified")).toBe(
      "Tue, 28 Jul 2026 00:00:00 GMT"
    );
  });

  it("uses the mutable R2 token authority to revoke the Worker URL", async () => {
    const replacement = "replacement-feed-token-with-sufficient-entropy";
    const env = environment(await hashToken(replacement));
    const revoked = await routeRequest(
      new Request(`https://podcast.test/feed/${token}.xml`),
      env
    );
    const current = await routeRequest(
      new Request(`https://podcast.test/feed/${replacement}.xml`),
      env
    );
    expect(revoked.status).toBe(404);
    expect(current.status).toBe(200);
  });

  it("serves suffix byte ranges with a valid partial response", async () => {
    const response = await routeRequest(
      new Request(
        `https://podcast.test/media/${token}/episodes/guid/v1/audio.m4a`,
        { headers: { Range: "bytes=-4" } }
      ),
      environment()
    );

    expect(response.status).toBe(206);
    expect(response.headers.get("Content-Type")).toBe("audio/x-m4a");
    expect(response.headers.get("Content-Range")).toBe("bytes 6-9/10");
    expect(response.headers.get("Content-Length")).toBe("4");
    expect(await response.text()).toBe("udio");
  });

  it("serves explicit byte ranges when R2 exposes an undefined suffix", async () => {
    const response = await routeRequest(
      new Request(
        `https://podcast.test/media/${token}/episodes/guid/v1/audio.m4a`,
        { headers: { Range: "bytes=0-1" } }
      ),
      environment()
    );

    expect(response.status).toBe(206);
    expect(response.headers.get("Content-Range")).toBe("bytes 0-1/10");
    expect(response.headers.get("Content-Length")).toBe("2");
    expect(await response.text()).toBe("fu");
  });
});
