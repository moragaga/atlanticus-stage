from __future__ import annotations

from types import TracebackType

from atlanticus.connectivity.http import HttpClient
from atlanticus.integrations.pi.web_api.errors import PiWebApiConfigurationError
from atlanticus.integrations.pi.web_api.points import PiPointResource
from atlanticus.integrations.pi.web_api.settings import PiWebApiSettings
from atlanticus.integrations.pi.web_api.streamsets import PiStreamSetResource
from atlanticus.integrations.pi.web_api.transport import PiWebApiTransport


class PiWebApiClient:
    def __init__(self, *, settings: PiWebApiSettings) -> None:
        if not isinstance(settings, PiWebApiSettings):
            raise PiWebApiConfigurationError('settings must be PiWebApiSettings')
        self.settings = settings
        self._http_client = HttpClient(settings=settings.http)
        self._transport = PiWebApiTransport(http_client=self._http_client)
        self.points = PiPointResource(transport=self._transport, settings=settings)
        self.streamsets = PiStreamSetResource(transport=self._transport, settings=settings)

    def __enter__(self) -> PiWebApiClient:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def open(self) -> None:
        self._http_client.open()

    def close(self) -> None:
        self._http_client.close()
