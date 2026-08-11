from __future__ import annotations

import unittest
from unittest.mock import patch

from atlanticus.kernel import Environment, EnvironmentName, InvalidEnvironmentError


class EnvironmentTests(unittest.TestCase):
    def test_only_official_values_are_accepted(self) -> None:
        expected_values = {
            'local': EnvironmentName.LOCAL,
            'dev': EnvironmentName.DEV,
            'uat': EnvironmentName.UAT,
            'stg': EnvironmentName.STG,
            'prd': EnvironmentName.PRD,
        }

        for value, expected in expected_values.items():
            with self.subTest(value=value):
                self.assertEqual(Environment.from_value(value).name, expected)

    def test_environment_enum_is_accepted_without_translation(self) -> None:
        environment = Environment.from_value(EnvironmentName.STG)

        self.assertEqual(environment.name, EnvironmentName.STG)
        self.assertEqual(str(environment), 'stg')

    def test_direct_constructor_rejects_non_enum_name(self) -> None:
        with self.assertRaises(InvalidEnvironmentError):
            Environment(name='prd')  # type: ignore[arg-type]

    def test_missing_and_blank_values_fail_explicitly(self) -> None:
        for value in (None, ''):
            with self.subTest(value=value):
                with self.assertRaises(InvalidEnvironmentError):
                    Environment.from_value(value)

    def test_aliases_and_old_values_are_rejected(self) -> None:
        invalid_values = (
            'prod',
            'production',
            'qa',
            'test',
            'testing',
            'stage',
            'staging',
        )

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(InvalidEnvironmentError):
                    Environment.from_value(value)

    def test_case_and_whitespace_are_not_normalized(self) -> None:
        for value in ('UAT', ' uat', 'uat '):
            with self.subTest(value=value):
                with self.assertRaises(InvalidEnvironmentError):
                    Environment.from_value(value)

    def test_unknown_value_reports_exact_contract(self) -> None:
        with self.assertRaises(InvalidEnvironmentError) as captured:
            Environment.from_value('production-east')

        self.assertEqual(captured.exception.value, 'production-east')
        self.assertEqual(
            captured.exception.allowed_values,
            ('local', 'dev', 'uat', 'stg', 'prd'),
        )
        self.assertEqual(
            str(captured.exception),
            "Invalid environment 'production-east'. Allowed values: local, dev, uat, stg, prd.",
        )

    def test_mapping_only_reads_environment_variable(self) -> None:
        environment = Environment.from_mapping(
            {
                'ENVIRONMENT': 'stg',
                'APP_NAME': 'IO',
            }
        )

        self.assertEqual(environment.name, EnvironmentName.STG)

    def test_missing_environment_in_mapping_fails(self) -> None:
        with self.assertRaises(InvalidEnvironmentError):
            Environment.from_mapping({'APP_NAME': 'IO'})

    def test_from_os_reads_environment_variable(self) -> None:
        with patch.dict('os.environ', {'ENVIRONMENT': 'prd'}, clear=True):
            environment = Environment.from_os()

        self.assertTrue(environment.is_production)
        self.assertFalse(environment.is_local)

    def test_environment_properties_keep_uat_and_stg_distinct(self) -> None:
        uat = Environment.from_value('uat')
        stg = Environment.from_value('stg')

        self.assertTrue(uat.is_uat)
        self.assertFalse(uat.is_stg)
        self.assertTrue(stg.is_stg)
        self.assertFalse(stg.is_uat)


if __name__ == '__main__':
    unittest.main()
