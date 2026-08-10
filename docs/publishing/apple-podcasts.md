# Apple Podcasts and Private RSS

TransPodcast publishes a private RSS feed. That feed can be followed in Apple
Podcasts by URL after the TransPodcast host has generated a feed token and at least one
episode has been approved for publication.

This is different from submitting a public show to the Apple Podcasts catalog.

## Private Apple Podcasts subscription

Use this path for personal learning or other noncommercial research.

1. Complete the TransPodcast host, speech pipeline, and publication storage setup.
2. Process a source episode for learning or research.
3. Listen to the private preview in TransPodcast Host.
4. Approve publication.
5. Open **Settings** in TransPodcast Host.
6. Copy the private subscription URL or use the Apple Podcasts open action.
7. In Apple Podcasts, follow the show by URL.

Treat the full feed URL as a secret. It contains the feed token. If the URL is
exposed, rotate it locally:

```shell
curl --fail --request POST \
  http://transpodcast.localhost:8000/api/v1/feed-token:revoke
```

Do not copy the command output into a repository, log, or support request. The
old URL becomes invalid immediately. Open **Settings** in TransPodcast Host,
copy the replacement URL, and follow it again in each podcast client.

## Public Apple Podcasts catalog

TransPodcast is designed for private RSS subscriptions used for learning and
research. Publishing translated episodes in the public Apple Podcasts catalog
is not part of the supported workflow and is not recommended.

If you independently choose to submit a show publicly, use Apple Podcasts
Connect and follow Apple's current feed, metadata, review, and content policies.
The private feed token is not suitable for a public catalog submission.

## References

- [Apple: How Apple Podcasts distributes your shows to listeners](https://podcasters.apple.com/support/5108-how-apple-podcasts-distributes-your-shows-to-listeners)
- [Apple: Podcast RSS feed requirements](https://podcasters.apple.com/support/823-podcast-requirements)
- [Apple: Submit a new show](https://podcasters.apple.com/support/897-submit-a-show)
