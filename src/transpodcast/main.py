from __future__ import annotations

from transpodcast.api import create_app
from transpodcast.config import TransPodcastSettings
from transpodcast.owner_console import OwnerConsoleStaticFiles
from transpodcast.production import build_dependencies

settings = TransPodcastSettings()
app = create_app(build_dependencies(settings))
if settings.owner_console_dist.is_dir():
    app.mount(
        "/",
        OwnerConsoleStaticFiles(
            directory=settings.owner_console_dist,
            html=True,
        ),
        name="owner-console",
    )
