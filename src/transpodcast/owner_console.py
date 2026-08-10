from __future__ import annotations

from pathlib import PurePosixPath

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


class OwnerConsoleStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code != 404 or not self._is_client_route(path):
                raise
            return await super().get_response("index.html", scope)

    @staticmethod
    def _is_client_route(path: str) -> bool:
        normalized_path = path.lstrip("/")
        if normalized_path.startswith("api/"):
            return False
        return PurePosixPath(normalized_path).suffix == ""
