# Apple Podcasts and Private RSS

TransPodcast publishes a private RSS feed. That feed can be followed in Apple
Podcasts by URL after the TransPodcast host has generated a feed token and at least one
episode has been approved for publication.

This is different from submitting a public show to the Apple Podcasts catalog.

## Private Apple Podcasts subscription

Use this path when TransPodcast is for personal listening, a private team, or
another controlled audience.

1. Complete the TransPodcast host, speech pipeline, and publication storage setup.
2. Process an authorized source episode.
3. Listen to the private preview in the Owner Console.
4. Approve publication.
5. Open **Settings** in the Owner Console.
6. Copy the private subscription URL or use the Apple Podcasts open action.
7. In Apple Podcasts, follow the show by URL.

Treat the full feed URL as a secret. It contains the feed token. If the URL is
exposed, rotate the token and restart the API and worker.

## Public Apple Podcasts Connect submission

Use this path only if you intend to list the translated show publicly and have
all content, voice, disclosure, and platform rights required for public
distribution.

1. Configure a stable HTTPS publication domain.
2. Confirm the Worker supports RSS reads, audio reads, HTTP HEAD, and byte-range
   media requests.
3. Publish a representative episode.
4. Validate the RSS feed against Apple Podcasts requirements.
5. Create or use an Apple Podcasts Connect account.
6. Add a new show with an RSS feed in Apple Podcasts Connect.
7. Complete Apple's content-rights, show-contact, artwork, metadata, and review
   requirements.

TransPodcast sets RSS fields required for private listening, including channel
metadata, episode GUIDs, enclosures, iTunes tags, artwork, and chapters. Public
catalog submission may require additional operational review, rights evidence,
and platform-specific metadata.

## References

- [Apple: How Apple Podcasts distributes your shows to listeners](https://podcasters.apple.com/support/5108-how-apple-podcasts-distributes-your-shows-to-listeners)
- [Apple: Podcast RSS feed requirements](https://podcasters.apple.com/support/823-podcast-requirements)
- [Apple: Submit a new show](https://podcasters.apple.com/support/897-submit-a-show)
