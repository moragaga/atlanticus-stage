from __future__ import annotations

import atlanticus.connectivity.http as http


def test_public_api_and_version_are_stable() -> None:
    assert http.__version__ == '0.1.0'
    assert http.__all__ == [
        'HttpAuthMode',
        'HttpClient',
        'HttpConfigurationError',
        'HttpConnectionError',
        'HttpError',
        'HttpRequestError',
        'HttpResponse',
        'HttpResponseError',
        'HttpSettings',
        'HttpStatusError',
        'HttpStreamError',
        'HttpStreamResult',
        'HttpTimeoutError',
        'HttpTimeoutPhase',
        '__version__',
    ]
