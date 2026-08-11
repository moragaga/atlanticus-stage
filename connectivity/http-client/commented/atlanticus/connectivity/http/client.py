"""Cliente HTTP síncrono con pooling, timeouts estrictos y streaming acotado."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Any, BinaryIO
from urllib.parse import unquote, urlsplit

import httpx

from atlanticus.connectivity.http.errors import (
    HttpConfigurationError,
    HttpConnectionError,
    HttpError,
    HttpRequestError,
    HttpResponseError,
    HttpStatusError,
    HttpStreamError,
    HttpTimeoutError,
)
from atlanticus.connectivity.http.models import (
    HttpAuthMode,
    HttpResponse,
    HttpStreamResult,
    HttpTimeoutPhase,
)
from atlanticus.connectivity.http.settings import HttpSettings
from atlanticus.observability import ErrorInfo, ResultSummary, runtime_guard

_COMPONENT = 'atlanticus.connectivity.http'
_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_SUPPORTED_METHODS = frozenset({'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'})
_PROTECTED_HEADERS = frozenset({'authorization', 'proxy-authorization'})
_READ_CHUNK_SIZE = 64 * 1024
_UNSET = object()


def _safe_parameters(args: tuple[Any, ...], values: Mapping[str, Any]) -> Mapping[str, Any]:
    # Solo se proyectan campos de baja cardinalidad; nunca destino, headers ni payload.
    instance = args[0] if args else None
    settings = getattr(instance, 'settings', None)
    safe: dict[str, Any] = {}
    if isinstance(settings, HttpSettings):
        safe['auth_mode'] = settings.auth_mode.value
        if settings.suffix is not None:
            safe['configuration_suffix'] = settings.suffix
    method = values.get('method')
    if isinstance(method, str):
        safe['method'] = method.strip().upper()
    return safe


def _safe_error(error: BaseException) -> ErrorInfo:
    # Los HttpError contienen mensajes propios; el resto pasa por el sanitizador transversal.
    if isinstance(error, HttpError):
        return ErrorInfo(error_type=type(error).__name__, message=str(error))
    return ErrorInfo.from_exception(error)


def _response_result(value: Any) -> ResultSummary:
    if not isinstance(value, HttpResponse):
        return ResultSummary()
    return ResultSummary(
        attributes={'status_code': value.status_code},
        metrics={'size_bytes': len(value.content)},
    )


def _stream_result(value: Any) -> ResultSummary:
    if not isinstance(value, HttpStreamResult):
        return ResultSummary()
    return ResultSummary(
        attributes={'status_code': value.status_code},
        metrics={'size_bytes': value.bytes_transferred},
    )


class HttpClient:
    """Reutiliza una conexión HTTP sin conocer APIs ni políticas de reintento."""

    def __init__(self, *, settings: HttpSettings) -> None:
        if not isinstance(settings, HttpSettings):
            raise HttpConfigurationError('settings must be HttpSettings')
        self.settings = settings
        self._client: httpx.Client | None = None
        self._closed = False

    def __enter__(self) -> HttpClient:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self.close()
        except HttpConnectionError:
            # Un fallo de cierre no reemplaza el error que originó la salida del contexto.
            if exc_value is None:
                raise

    def open(self) -> None:
        """Crea un único pool reutilizable sin ejecutar todavía una solicitud."""

        if self._closed:
            raise HttpConnectionError('HTTP client is closed')
        if self._client is not None:
            return
        try:
            self._client = httpx.Client(
                base_url=self.settings.base_url,
                auth=_build_auth(self.settings),
                headers=_build_default_headers(self.settings),
                timeout=httpx.Timeout(
                    connect=self.settings.connect_timeout_seconds,
                    read=self.settings.read_timeout_seconds,
                    write=self.settings.write_timeout_seconds,
                    pool=self.settings.pool_timeout_seconds,
                ),
                verify=self.settings.verify_tls,
                follow_redirects=False,
                trust_env=False,
            )
        except Exception:
            raise HttpConnectionError('Could not create HTTP client') from None

    @runtime_guard(
        operation='http.request',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_response_result,
        error_mapper=_safe_error,
    )
    def request(
        self,
        method: str,
        endpoint: str = '',
        *,
        params: Mapping[str, Any] | Sequence[tuple[str, Any]] | None = None,
        headers: Mapping[str, str] | None = None,
        json_data: Any = _UNSET,
        content: bytes | bytearray | memoryview | str | None = None,
    ) -> HttpResponse:
        """Ejecuta una solicitud y carga una respuesta acotada en memoria."""

        normalized_method, normalized_endpoint, normalized_headers = _normalize_request(
            method=method,
            endpoint=endpoint,
            params=params,
            headers=headers,
            json_data=json_data,
            content=content,
        )
        response = self._send(
            method=normalized_method,
            endpoint=normalized_endpoint,
            params=params,
            headers=normalized_headers,
            json_data=json_data,
            content=content,
        )
        try:
            # Se valida el tamaño declarado antes de consumir el primer bloque.
            _ensure_success(response=response, method=normalized_method)
            _ensure_declared_size(
                response=response,
                max_response_bytes=self.settings.max_response_bytes,
            )
            body = bytearray()
            for chunk in response.iter_bytes(chunk_size=_READ_CHUNK_SIZE):
                # El conteo real protege incluso cuando Content-Length está ausente o es falso.
                if len(body) + len(chunk) > self.settings.max_response_bytes:
                    raise HttpResponseError('HTTP response exceeds max_response_bytes')
                body.extend(chunk)
            return HttpResponse(
                status_code=response.status_code,
                method=normalized_method,
                headers=dict(response.headers),
                content=body,
            )
        except httpx.TimeoutException as error:
            raise _timeout_error(error) from None
        except HttpError:
            raise
        except httpx.HTTPError:
            raise HttpConnectionError('HTTP response failed') from None
        except Exception:
            raise HttpConnectionError('HTTP response failed') from None
        finally:
            _close_response(response)

    def request_json(self, method: str, endpoint: str = '', **kwargs: Any) -> Any:
        """Ejecuta una solicitud acotada y decodifica cualquier valor JSON."""

        return self.request(method, endpoint, **kwargs).decode_json()

    def request_text(
        self,
        method: str,
        endpoint: str = '',
        *,
        encoding: str = 'utf-8',
        **kwargs: Any,
    ) -> str:
        """Ejecuta una solicitud acotada y decodifica su cuerpo como texto."""

        return self.request(method, endpoint, **kwargs).decode_text(encoding=encoding)

    def request_bytes(self, method: str, endpoint: str = '', **kwargs: Any) -> bytes:
        """Ejecuta una solicitud acotada y retorna una copia de sus bytes."""

        return self.request(method, endpoint, **kwargs).content

    @runtime_guard(
        operation='http.stream_to',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_stream_result,
        error_mapper=_safe_error,
    )
    def stream_to(
        self,
        *,
        stream: BinaryIO,
        method: str = 'GET',
        endpoint: str = '',
        params: Mapping[str, Any] | Sequence[tuple[str, Any]] | None = None,
        headers: Mapping[str, str] | None = None,
        json_data: Any = _UNSET,
        content: bytes | bytearray | memoryview | str | None = None,
        chunk_size: int = _READ_CHUNK_SIZE,
    ) -> HttpStreamResult:
        """Transfiere la respuesta por bloques hacia un stream del consumidor."""

        if not callable(getattr(stream, 'write', None)):
            raise HttpRequestError('stream must provide write()')
        if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
            raise HttpRequestError('chunk_size must be an integer greater than zero')
        normalized_method, normalized_endpoint, normalized_headers = _normalize_request(
            method=method,
            endpoint=endpoint,
            params=params,
            headers=headers,
            json_data=json_data,
            content=content,
        )
        bytes_transferred = 0
        response = self._send(
            method=normalized_method,
            endpoint=normalized_endpoint,
            params=params,
            headers=normalized_headers,
            json_data=json_data,
            content=content,
        )
        try:
            _ensure_success(response=response, method=normalized_method)
            for chunk in response.iter_bytes(chunk_size=chunk_size):
                try:
                    # La escritura pertenece al consumidor y cualquier fallo se neutraliza aquí.
                    written = stream.write(chunk)
                except Exception:
                    raise HttpStreamError(bytes_transferred=bytes_transferred) from None
                if isinstance(written, bool) or not isinstance(written, int):
                    raise HttpStreamError(bytes_transferred=bytes_transferred)
                if not 0 <= written <= len(chunk):
                    raise HttpStreamError(bytes_transferred=bytes_transferred)
                bytes_transferred += written
                if written != len(chunk):
                    raise HttpStreamError(bytes_transferred=bytes_transferred)
            return HttpStreamResult(
                status_code=response.status_code,
                method=normalized_method,
                bytes_transferred=bytes_transferred,
                headers=dict(response.headers),
            )
        except httpx.TimeoutException as error:
            raise _timeout_error(error, bytes_transferred=bytes_transferred) from None
        except HttpError:
            raise
        except httpx.HTTPError:
            if bytes_transferred:
                raise HttpStreamError(bytes_transferred=bytes_transferred) from None
            raise HttpConnectionError('HTTP request failed') from None
        except Exception:
            if bytes_transferred:
                raise HttpStreamError(bytes_transferred=bytes_transferred) from None
            raise HttpConnectionError('HTTP request failed') from None
        finally:
            _close_response(response)

    def close(self) -> None:
        """Cierra el pool de conexiones de forma idempotente."""

        client = self._client
        # Se marca cerrado antes de invocar al SDK para que un fallo no permita reabrirlo.
        self._client = None
        self._closed = True
        if client is not None:
            try:
                client.close()
            except Exception:
                raise HttpConnectionError('Could not close HTTP client') from None

    def _require_client(self) -> httpx.Client:
        self.open()
        if self._client is None:
            raise HttpConnectionError('HTTP client is not open')
        return self._client

    def _send(
        self,
        *,
        method: str,
        endpoint: str,
        params: Mapping[str, Any] | Sequence[tuple[str, Any]] | None,
        headers: Mapping[str, str] | None,
        json_data: Any,
        content: bytes | bytearray | memoryview | str | None,
    ) -> httpx.Response:
        client = self._require_client()
        try:
            # Construir y enviar se separan para clasificar errores locales y de red.
            request = client.build_request(
                method,
                endpoint,
                params=params,
                headers=headers,
                json=None if json_data is _UNSET else json_data,
                content=_normalize_content(content),
            )
        except Exception:
            raise HttpRequestError('Could not build HTTP request') from None
        try:
            return client.send(request, stream=True)
        except httpx.TimeoutException as error:
            raise _timeout_error(error) from None
        except httpx.HTTPError:
            raise HttpConnectionError('HTTP request failed') from None
        except Exception:
            raise HttpConnectionError('HTTP request failed') from None


def _build_auth(settings: HttpSettings) -> httpx.Auth | None:
    if settings.auth_mode == HttpAuthMode.BASIC:
        return httpx.BasicAuth(
            username=_require_secret(settings.username),
            password=_require_secret(settings.password),
        )
    return None


def _build_default_headers(settings: HttpSettings) -> Mapping[str, str] | None:
    if settings.auth_mode == HttpAuthMode.BEARER:
        return {'Authorization': f'Bearer {_require_secret(settings.bearer_token)}'}
    return None


def _require_secret(value: str | None) -> str:
    if value is None:
        raise HttpConnectionError('HTTP authentication settings are incomplete')
    return value


def _normalize_request(
    *,
    method: str,
    endpoint: str,
    params: Mapping[str, Any] | Sequence[tuple[str, Any]] | None,
    headers: Mapping[str, str] | None,
    json_data: Any,
    content: bytes | bytearray | memoryview | str | None,
) -> tuple[str, str, Mapping[str, str] | None]:
    normalized_method = _normalize_method(method)
    normalized_endpoint = _normalize_endpoint(endpoint)
    normalized_headers = _normalize_headers(headers)
    _validate_params(params)
    if json_data is not _UNSET and content is not None:
        raise HttpRequestError('json_data and content cannot be sent together')
    if json_data is not _UNSET:
        _validate_json_data(json_data)
    if content is not None and not isinstance(content, bytes | bytearray | memoryview | str):
        raise HttpRequestError('content must be bytes-like or text')
    return normalized_method, normalized_endpoint, normalized_headers


def _normalize_method(value: Any) -> str:
    if not isinstance(value, str):
        raise HttpRequestError('method must be a supported standard HTTP method')
    normalized = value.strip().upper()
    if normalized not in _SUPPORTED_METHODS:
        raise HttpRequestError('method must be a supported standard HTTP method')
    return normalized


def _normalize_endpoint(value: Any) -> str:
    if not isinstance(value, str):
        raise HttpRequestError('endpoint must be text')
    normalized = value.strip()
    try:
        parsed = urlsplit(normalized)
    except ValueError:
        raise HttpRequestError('endpoint must be a valid relative path') from None
    if parsed.scheme or parsed.netloc:
        raise HttpRequestError('endpoint must be relative to base_url')
    if parsed.query or parsed.fragment:
        raise HttpRequestError('endpoint must not contain query parameters or fragments')
    decoded_path = unquote(parsed.path)
    # Los caracteres de control podrían alterar la línea de solicitud o metadatos HTTP.
    if any(ord(character) < 32 or ord(character) == 127 for character in decoded_path):
        raise HttpRequestError('endpoint must not contain control characters')
    segments = [segment for segment in decoded_path.split('/') if segment]
    if '..' in segments or '\\' in decoded_path:
        raise HttpRequestError('endpoint must not escape base_url')
    return parsed.path.lstrip('/')


def _normalize_headers(headers: Mapping[str, str] | None) -> Mapping[str, str] | None:
    if headers is None:
        return None
    if not isinstance(headers, Mapping):
        raise HttpRequestError('headers must be a mapping')
    normalized: dict[str, str] = {}
    for name, value in headers.items():
        if not isinstance(name, str) or _HEADER_NAME_PATTERN.fullmatch(name) is None:
            raise HttpRequestError('header names contain invalid characters')
        if not isinstance(value, str) or '\r' in value or '\n' in value:
            raise HttpRequestError('header values must be text without line breaks')
        if name.lower() in _PROTECTED_HEADERS:
            raise HttpRequestError('Authorization headers must be configured through auth_mode')
        normalized[name] = value
    return normalized


def _validate_params(value: Any) -> None:
    if value is None or isinstance(value, Mapping):
        return
    if isinstance(value, str | bytes | bytearray) or not isinstance(value, Sequence):
        raise HttpRequestError('params must be a mapping or a sequence of pairs')
    for item in value:
        if not isinstance(item, tuple | list) or len(item) != 2:
            raise HttpRequestError('params must be a mapping or a sequence of pairs')


def _validate_json_data(value: Any) -> None:
    try:
        # HTTPX serializará después; esta pasada solo verifica el contrato finito local.
        json.dumps(value, allow_nan=False)
    except TypeError, ValueError, OverflowError:
        raise HttpRequestError('json_data must contain valid finite JSON values') from None


def _normalize_content(
    value: bytes | bytearray | memoryview | str | None,
) -> bytes | str | None:
    if isinstance(value, bytearray | memoryview):
        return bytes(value)
    return value


def _ensure_success(*, response: httpx.Response, method: str) -> None:
    if 200 <= response.status_code < 300:
        return
    raise HttpStatusError(status_code=response.status_code, method=method)


def _ensure_declared_size(*, response: httpx.Response, max_response_bytes: int) -> None:
    value = response.headers.get('Content-Length')
    if value is None:
        return
    try:
        content_length = int(value)
    except ValueError:
        raise HttpResponseError('HTTP response has invalid Content-Length') from None
    if content_length < 0:
        raise HttpResponseError('HTTP response has invalid Content-Length')
    if content_length > max_response_bytes:
        raise HttpResponseError('HTTP response exceeds max_response_bytes')


def _close_response(response: httpx.Response) -> None:
    # sys.exception conserva el error activo que un fallo secundario de close no debe ocultar.
    active_error = sys.exception()
    try:
        response.close()
    except Exception:
        if active_error is None:
            raise HttpConnectionError('Could not close HTTP response') from None


def _timeout_error(
    error: httpx.TimeoutException,
    *,
    bytes_transferred: int = 0,
) -> HttpTimeoutError:
    if isinstance(error, httpx.ConnectTimeout):
        phase = HttpTimeoutPhase.CONNECT
    elif isinstance(error, httpx.WriteTimeout):
        phase = HttpTimeoutPhase.WRITE
    elif isinstance(error, httpx.PoolTimeout):
        phase = HttpTimeoutPhase.POOL
    else:
        phase = HttpTimeoutPhase.READ
    return HttpTimeoutError(phase=phase, bytes_transferred=bytes_transferred)
