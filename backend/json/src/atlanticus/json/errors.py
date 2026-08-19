from __future__ import annotations


class JsonError(Exception):
    pass


class JsonValidationError(JsonError, ValueError):
    pass


class JsonReadError(JsonError, OSError):
    pass


class JsonWriteError(JsonError, OSError):
    pass


class JsonCorruptionError(JsonError):
    pass


class JsonConflictError(JsonWriteError):
    pass
