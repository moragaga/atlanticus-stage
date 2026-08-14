# Adaptador interno entre atlanticus-http y el dominio PI Web API.
# Traduce fallos para distinguir timeout, estado HTTP y respuesta inválida.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from atlanticus.connectivity.http import (
    HttpClient,
    HttpConnectionError,
    HttpError,
    HttpRequestError,
    HttpResponseError,
    HttpStatusError,
    HttpTimeoutError,
)
from atlanticus.integrations.pi.web_api.errors import (
    PiWebApiConnectionError,
    PiWebApiRequestError,
    PiWebApiResponseError,
    PiWebApiStatusError,
    PiWebApiTimeoutError,
)


class PiWebApiTransport:
    def __init__(self, *, http_client: HttpClient) -> None:
        if not isinstance(http_client, HttpClient):
            raise TypeError('http_client must be HttpClient')
        self._http_client = http_client

    def get_json(
        self,
        endpoint: str,
        *,
        params: Mapping[str, Any] | Sequence[tuple[str, Any]] | None = None,
    ) -> Any:
        try:
            return self._http_client.request_json('GET', endpoint, params=params)
        except HttpTimeoutError as error:
            raise PiWebApiTimeoutError(phase=error.phase.value) from None
        except HttpStatusError as error:
            raise PiWebApiStatusError(status_code=error.status_code) from None
        except HttpRequestError:
            raise PiWebApiRequestError('Could not build PI Web API request') from None
        except HttpResponseError:
            raise PiWebApiResponseError('PI Web API returned an invalid HTTP response') from None
        except HttpConnectionError:
            raise PiWebApiConnectionError('PI Web API request failed') from None
        except HttpError:
            raise PiWebApiConnectionError('PI Web API request failed') from None
