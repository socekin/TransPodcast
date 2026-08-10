# Cloudflare Private Publication

Remote publication uses a private R2 bucket plus the Worker in
`workers/publication/`. The Worker checks the feed token before serving RSS or
media. The R2 bucket itself must not be publicly accessible.

## Preparation

1. Install the Worker dependencies and authenticate Wrangler:

   ```shell
   npm --prefix workers/publication ci
   npm --prefix workers/publication exec -- wrangler login
   ```

2. Create dedicated production and preview R2 buckets:

   ```shell
   npm --prefix workers/publication exec -- wrangler r2 bucket create \
     private-transpodcast
   npm --prefix workers/publication exec -- wrangler r2 bucket create \
     private-transpodcast-preview
   ```

3. Create a TransPodcast host R2 S3 API token scoped only to the production bucket,
   following Cloudflare's [R2 token guide](https://developers.cloudflare.com/r2/api/tokens/).
   Put its access key ID, secret access key, account endpoint, and bucket name
   only in the TransPodcast host `.env`.
4. Update the placeholder bucket names and custom domain in
   `workers/publication/wrangler.jsonc`.
5. Confirm the domain is in an active Cloudflare zone and has no conflicting
   CNAME. Wrangler creates the custom-domain DNS record and certificate during
   deployment.
6. Review logs and sampling settings for metadata exposure and cost.

Keep account IDs, API tokens, feed tokens, and real domain values out of Git.
Use Worker bindings and secret management for deployed configuration.

## Verify before publishing

- the bucket has no public development URL;
- a request without a token is rejected;
- an incorrect or revoked token is rejected;
- the correct token can read the feed and only referenced immutable media;
- preview expiry is enforced;
- logs do not record query tokens or complete private feed URLs.

After reviewing the diff and active account, deploy explicitly:

```shell
npm --prefix workers/publication run deploy
```

Deployment changes external state and is never run by the test suite. See
Cloudflare's [custom-domain guide](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/)
for DNS constraints and certificate behavior.
