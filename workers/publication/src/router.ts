const FEED_OBJECT_KEY = "feed/current.xml";
const PUBLICATION_PREFIX = "publications/";

export async function routeRequest(
  request: Request,
  env: Env,
): Promise<Response> {
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response("Method not allowed", {
      status: 405,
      headers: { Allow: "GET, HEAD" },
    });
  }

  const url = new URL(request.url);
  if (url.pathname === "/") {
    return serveHomepage(request);
  }
  const feedMatch = /^\/feed\/([^/]+)\.xml$/.exec(url.pathname);
  if (feedMatch) {
    const token = feedMatch[1];
    if (!token || !(await tokenIsAuthorized(token, env))) {
      return new Response("Not found", { status: 404 });
    }
    return serveFeed(request, env);
  }

  const mediaMatch = /^\/media\/([^/]+)\/(.+)$/.exec(url.pathname);
  if (mediaMatch) {
    const token = mediaMatch[1];
    const mediaKey = mediaMatch[2];
    if (
      !token ||
      !mediaKey ||
      !(await tokenIsAuthorized(token, env)) ||
      !isSafeMediaKey(mediaKey)
    ) {
      return new Response("Not found", { status: 404 });
    }
    return serveMedia(request, env, mediaKey);
  }

  return new Response("Not found", { status: 404 });
}

function serveHomepage(request: Request): Response {
  const body = [
    "<!doctype html>",
    '<html lang="en">',
    "<head>",
    '<meta charset="utf-8">',
    '<meta name="viewport" content="width=device-width,initial-scale=1">',
    "<title>TransPodcast</title>",
    "</head>",
    "<body>",
    "<main>",
    "<h1>TransPodcast</h1>",
    "<p>Private complete Mandarin podcast editions.</p>",
    "</main>",
    "</body>",
    "</html>",
  ].join("");
  const headers = new Headers({
    "Cache-Control": "public, max-age=3600",
    "Content-Security-Policy": "default-src 'none'; style-src 'none'",
    "Content-Type": "text/html; charset=utf-8",
    "X-Content-Type-Options": "nosniff",
  });
  if (request.method === "GET") {
    headers.set(
      "Content-Length",
      new TextEncoder().encode(body).byteLength.toString(),
    );
  }
  return new Response(request.method === "HEAD" ? null : body, { headers });
}

async function tokenIsAuthorized(
  actual: string,
  env: Env,
): Promise<boolean> {
  const authority = await env.MEDIA.get(
    "configuration/feed-token.sha256",
  );
  if (authority === null) {
    return tokensMatch(actual, env.FEED_TOKEN);
  }
  const expectedHash = (await authority.text()).trim().toLowerCase();
  const actualHash = await hashToken(actual);
  return constantTimeTextMatch(actualHash, expectedHash);
}

async function serveFeed(request: Request, env: Env): Promise<Response> {
  const object = await env.MEDIA.get(FEED_OBJECT_KEY);
  if (object === null) {
    return new Response("Not found", { status: 404 });
  }
  const headers = new Headers({
    "Cache-Control": "private, no-store",
    "Content-Length": object.size.toString(),
    "Content-Type": "application/rss+xml; charset=utf-8",
    ETag: object.httpEtag,
    "X-Content-Type-Options": "nosniff",
  });
  return new Response(request.method === "HEAD" ? null : object.body, {
    headers,
  });
}

async function serveMedia(
  request: Request,
  env: Env,
  mediaKey: string,
): Promise<Response> {
  const key = `${PUBLICATION_PREFIX}${mediaKey}`;
  if (request.method === "HEAD") {
    const metadata = await env.MEDIA.head(key);
    if (metadata === null) {
      return new Response("Not found", { status: 404 });
    }
    const headers = mediaHeaders(metadata, mediaKey);
    return new Response(null, { headers });
  }

  const range = request.headers.get("Range");
  const object = await env.MEDIA.get(
    key,
    range ? { range: request.headers } : undefined,
  );
  if (object === null) {
    return new Response("Not found", { status: 404 });
  }
  const headers = mediaHeaders(object, mediaKey);
  let status = 200;
  if (object.range) {
    const range = object.range;
    const suffix =
      "suffix" in range && typeof range.suffix === "number"
        ? range.suffix
        : null;
    const explicitOffset =
      "offset" in range && typeof range.offset === "number" ? range.offset : 0;
    const explicitLength =
      "length" in range && typeof range.length === "number"
        ? range.length
        : object.size - explicitOffset;
    const offset =
      suffix === null
        ? explicitOffset
        : Math.max(0, object.size - suffix);
    const length =
      suffix === null
        ? explicitLength
        : Math.min(suffix, object.size);
    headers.set(
      "Content-Range",
      `bytes ${offset}-${offset + length - 1}/${object.size}`,
    );
    headers.set("Content-Length", length.toString());
    status = 206;
  }
  return new Response(object.body, { status, headers });
}

function mediaHeaders(object: R2Object, mediaKey: string): Headers {
  const headers = new Headers({
    "Accept-Ranges": "bytes",
    "Cache-Control": "private, max-age=31536000, immutable",
    "Content-Length": object.size.toString(),
    ETag: object.httpEtag,
    "Last-Modified": object.uploaded.toUTCString(),
    "X-Content-Type-Options": "nosniff",
  });
  object.writeHttpMetadata(headers);
  if (mediaKey.endsWith(".m4a")) {
    headers.set("Content-Type", "audio/x-m4a");
  } else if (!headers.has("Content-Type")) {
    headers.set("Content-Type", contentTypeFor(mediaKey));
  }
  return headers;
}

function contentTypeFor(mediaKey: string): string {
  if (mediaKey.endsWith(".json")) {
    return "application/json+chapters";
  }
  if (mediaKey.endsWith(".png")) {
    return "image/png";
  }
  if (mediaKey.endsWith(".m4a")) {
    return "audio/x-m4a";
  }
  return "audio/mp4";
}

function isSafeMediaKey(value: string): boolean {
  if (value.includes("\\") || value.includes("\0")) {
    return false;
  }
  const segments = value.split("/");
  return segments.every(
    (segment) =>
      segment.length > 0 && segment !== "." && segment !== "..",
  );
}

export async function tokensMatch(
  actual: string,
  expected: string,
): Promise<boolean> {
  const encoder = new TextEncoder();
  const [actualHash, expectedHash] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(actual)),
    crypto.subtle.digest("SHA-256", encoder.encode(expected)),
  ]);
  const actualBytes = new Uint8Array(actualHash);
  const expectedBytes = new Uint8Array(expectedHash);
  let difference = actualBytes.length ^ expectedBytes.length;
  for (let index = 0; index < actualBytes.length; index += 1) {
    difference |=
      (actualBytes.at(index) ?? 0) ^ (expectedBytes.at(index) ?? 0);
  }
  return difference === 0;
}

export async function hashToken(value: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function constantTimeTextMatch(actual: string, expected: string): boolean {
  const actualBytes = new TextEncoder().encode(actual);
  const expectedBytes = new TextEncoder().encode(expected);
  let difference = actualBytes.length ^ expectedBytes.length;
  for (let index = 0; index < actualBytes.length; index += 1) {
    difference |=
      (actualBytes.at(index) ?? 0) ^ (expectedBytes.at(index) ?? 0);
  }
  return difference === 0;
}
