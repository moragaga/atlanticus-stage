from __future__ import annotations

import json
import math
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from atlanticus.kernel import REDACTED, DataSanitizer


class DemoStatus(StrEnum):
    READY = 'ready'


@dataclass
class DemoRecord:
    name: str
    access_token: str


class ObjectWithSensitiveRepr:
    def __repr__(self) -> str:
        return 'password=must-not-be-rendered'


class ObjectWithUnsafeStringRepresentation:
    def __str__(self) -> str:
        raise AssertionError('__str__ must not be called')

    def __repr__(self) -> str:
        raise AssertionError('__repr__ must not be called')


class DataSanitizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sanitizer = DataSanitizer()

    def test_sensitive_values_are_redacted_recursively(self) -> None:
        payload = self.sanitizer.sanitize(
            {
                'user': 'demo',
                'credentials': {
                    'api_key': 'abc',
                },
            }
        )

        self.assertEqual(payload['user'], 'demo')
        self.assertEqual(payload['credentials'], REDACTED)

    def test_dataclass_fields_are_sanitized(self) -> None:
        payload = self.sanitizer.sanitize(DemoRecord(name='record-a', access_token='abc'))

        self.assertEqual(payload, {'name': 'record-a', 'access_token': REDACTED})

    def test_common_values_are_json_safe(self) -> None:
        payload = self.sanitizer.sanitize(
            {
                'created_at': datetime(2026, 7, 16, 10, 30, tzinfo=UTC),
                'elapsed': timedelta(seconds=12.5),
                'path': Path('/tmp/demo'),
                'amount': Decimal('10.50'),
                'identifier': UUID('12345678-1234-5678-1234-567812345678'),
                'status': DemoStatus.READY,
                'content': b'abc',
            }
        )

        self.assertEqual(payload['created_at'], '2026-07-16T10:30:00+00:00')
        self.assertEqual(payload['elapsed'], 12.5)
        self.assertEqual(payload['path'], '/tmp/demo')
        self.assertEqual(payload['amount'], '10.50')
        self.assertEqual(payload['identifier'], '12345678-1234-5678-1234-567812345678')
        self.assertEqual(payload['status'], 'ready')
        self.assertEqual(payload['content'], {'type': 'bytes', 'size_bytes': 3})
        json.dumps(payload, allow_nan=False)

    def test_non_finite_floats_are_valid_json_values(self) -> None:
        payload = self.sanitizer.sanitize(
            {
                'nan': math.nan,
                'positive': math.inf,
                'negative': -math.inf,
            }
        )

        self.assertEqual(
            payload,
            {
                'nan': 'NaN',
                'positive': 'Infinity',
                'negative': '-Infinity',
            },
        )
        json.dumps(payload, allow_nan=False)

    def test_collections_and_strings_are_truncated(self) -> None:
        sanitizer = DataSanitizer(max_items=2, max_string_length=4)

        payload = sanitizer.sanitize(
            {
                'text': 'abcdefgh',
                'values': [1, 2, 3],
            }
        )

        self.assertEqual(payload['text'], 'abcd...<truncated>')
        self.assertEqual(payload['values'], [1, 2, {'__truncated__': True}])

    def test_depth_is_bounded(self) -> None:
        sanitizer = DataSanitizer(max_depth=1)

        payload = sanitizer.sanitize({'outer': {'inner': {'value': 1}}})

        self.assertEqual(
            payload['outer']['inner'],
            {'type': 'dict', 'summary': 'max_depth_reached'},
        )

    def test_unknown_objects_do_not_expose_repr(self) -> None:
        payload = self.sanitizer.sanitize(ObjectWithSensitiveRepr())

        self.assertEqual(
            payload,
            {
                'type': 'ObjectWithSensitiveRepr',
                'summary': 'unsupported_object',
            },
        )
        self.assertNotIn('password', str(payload))

    def test_mapping_with_non_string_key_does_not_render_the_key(self) -> None:
        unsafe_key = ObjectWithUnsafeStringRepresentation()

        payload = self.sanitizer.sanitize({unsafe_key: 'secret-value'})

        self.assertEqual(
            payload,
            {
                'type': 'dict',
                'summary': 'non_string_key',
            },
        )

    def test_exception_is_summarized_without_its_message(self) -> None:
        payload = self.sanitizer.sanitize(
            RuntimeError('https://storage.example/data?sv=1&sig=sensitive-value')
        )

        self.assertEqual(payload, {'type': 'RuntimeError'})
        self.assertNotIn('sensitive-value', str(payload))

    def test_sensitive_keys_ignore_case_and_separators(self) -> None:
        payload = self.sanitizer.sanitize(
            {
                'ConnectionString': 'secret-a',
                'api-key': 'secret-b',
                'AccountKey': 'secret-c',
                'Authorization': 'secret-d',
                'sasUrl': 'secret-e',
                'sharedAccessSignature': 'secret-f',
                'status': 'ready',
            }
        )

        self.assertEqual(
            payload,
            {
                'ConnectionString': REDACTED,
                'api-key': REDACTED,
                'AccountKey': REDACTED,
                'Authorization': REDACTED,
                'sasUrl': REDACTED,
                'sharedAccessSignature': REDACTED,
                'status': 'ready',
            },
        )

    def test_custom_sensitive_key_parts_are_normalized(self) -> None:
        sanitizer = DataSanitizer(sensitive_key_parts=('client-secret',))

        payload = sanitizer.sanitize({'clientSecret': 'secret-value'})

        self.assertEqual(payload, {'clientSecret': REDACTED})

    def test_invalid_limits_fail_during_construction(self) -> None:
        invalid_arguments = (
            (
                {'max_depth': -1},
                'max_depth must be greater than or equal to zero.',
            ),
            ({'max_items': 0}, 'max_items must be greater than zero.'),
            (
                {'max_string_length': 0},
                'max_string_length must be greater than zero.',
            ),
            ({'sensitive_key_parts': ()}, 'sensitive_key_parts must not be empty.'),
            (
                {'sensitive_key_parts': ('   ',)},
                'sensitive_key_parts must contain only non-empty strings.',
            ),
            (
                {'sensitive_key_parts': ('-',)},
                'sensitive_key_parts must contain only non-empty strings.',
            ),
        )

        for arguments, expected_message in invalid_arguments:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError) as captured:
                DataSanitizer(**arguments)

            self.assertEqual(str(captured.exception), expected_message)


if __name__ == '__main__':
    unittest.main()
