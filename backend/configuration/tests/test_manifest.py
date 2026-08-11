from __future__ import annotations

import json

import pytest

from atlanticus.configuration import SecretManifestEntry, SecretsManifest, SecretsManifestError


def _write_manifest(tmp_path, document: object):
    path = tmp_path / 'secrets.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    return path


def test_manifest_preserves_the_corporate_schema_and_indexes_entries(tmp_path) -> None:
    path = _write_manifest(
        tmp_path,
        [
            {
                'var_name': 'TOPIC_NAME',
                'secret_name': None,
                'value': 'events',
                'exists_in_key_vault': False,
            },
            {
                'var_name': 'CONNECTION_STRING',
                'secret_name': 'secret-service-bus',
                'value': None,
                'exists_in_key_vault': True,
            },
        ],
    )

    manifest = SecretsManifest.from_path(path)

    assert manifest.find('TOPIC_NAME').value == 'events'
    assert manifest.find('CONNECTION_STRING').secret_name == 'secret-service-bus'
    assert dict(manifest.static_values()) == {'TOPIC_NAME': 'events'}
    assert 'events' not in repr(manifest.find('TOPIC_NAME'))


def test_manifest_uses_exists_in_key_vault_as_the_authoritative_source(tmp_path) -> None:
    path = _write_manifest(
        tmp_path,
        [
            {
                'var_name': 'STATIC_VALUE',
                'secret_name': 'ignored-static-secret',
                'value': '  static value  ',
                'exists_in_key_vault': False,
            },
            {
                'var_name': 'SECRET_VALUE',
                'secret_name': 'secret-value',
                'value': 'ignored-fallback',
                'exists_in_key_vault': True,
            },
        ],
    )

    manifest = SecretsManifest.from_path(path)

    assert manifest.find('STATIC_VALUE').secret_name == 'ignored-static-secret'
    assert manifest.find('STATIC_VALUE').value == '  static value  '
    assert manifest.find('SECRET_VALUE').value == 'ignored-fallback'
    assert dict(manifest.static_values()) == {'STATIC_VALUE': '  static value  '}


def test_manifest_preserves_whitespace_only_static_value(tmp_path) -> None:
    path = _write_manifest(
        tmp_path,
        [
            {
                'var_name': 'SPACE_VALUE',
                'secret_name': None,
                'value': ' ',
                'exists_in_key_vault': False,
            }
        ],
    )

    manifest = SecretsManifest.from_path(path)

    assert manifest.find('SPACE_VALUE').value == ' '
    assert dict(manifest.static_values()) == {'SPACE_VALUE': ' '}


@pytest.mark.parametrize(
    'document',
    [
        {},
        [{'var_name': 'TOKEN'}],
        [
            {
                'var_name': 'TOKEN',
                'secret_name': None,
                'value': None,
                'exists_in_key_vault': True,
            }
        ],
        [
            {
                'var_name': 'TOKEN',
                'secret_name': None,
                'value': '',
                'exists_in_key_vault': False,
            }
        ],
    ],
)
def test_invalid_manifest_is_rejected(tmp_path, document: object) -> None:
    with pytest.raises(SecretsManifestError):
        SecretsManifest.from_path(_write_manifest(tmp_path, document))


def test_duplicate_environment_variable_is_rejected(tmp_path) -> None:
    entry = {
        'var_name': 'TOPIC_NAME',
        'secret_name': None,
        'value': 'events',
        'exists_in_key_vault': False,
    }

    with pytest.raises(SecretsManifestError, match='TOPIC_NAME'):
        SecretsManifest.from_path(_write_manifest(tmp_path, [entry, entry]))


def test_missing_manifest_reports_only_its_path(tmp_path) -> None:
    missing = tmp_path / 'missing.json'

    with pytest.raises(SecretsManifestError, match='missing.json'):
        SecretsManifest.from_path(missing)


@pytest.mark.parametrize(
    'entry',
    [
        {
            'var_name': 'TOPIC',
            'secret_name': None,
            'value': 'events',
            'exists_in_key_vault': False,
            'unexpected': True,
        },
        {
            'var_name': 'TOPIC',
            'secret_name': None,
            'value': 123,
            'exists_in_key_vault': False,
        },
        {
            'var_name': 'TOKEN',
            'secret_name': 123,
            'value': None,
            'exists_in_key_vault': True,
        },
        {
            'var_name': 'TOKEN',
            'secret_name': 'secret-token',
            'value': None,
            'exists_in_key_vault': 'true',
        },
    ],
)
def test_manifest_rejects_unknown_ambiguous_or_mistyped_fields(tmp_path, entry) -> None:
    with pytest.raises(SecretsManifestError):
        SecretsManifest.from_path(_write_manifest(tmp_path, [entry]))


def test_manifest_builds_its_index_internally() -> None:
    entry = SecretManifestEntry(
        var_name='TOPIC',
        secret_name=None,
        value='events',
        exists_in_key_vault=False,
    )
    manifest = SecretsManifest(entries=(entry,))

    assert manifest.find('TOPIC') is entry
    with pytest.raises(TypeError):
        SecretsManifest(entries=(entry,), _by_variable={})  # type: ignore[call-arg]


def test_manifest_constructor_requires_immutable_validated_entries() -> None:
    with pytest.raises(SecretsManifestError):
        SecretsManifest(entries=[])  # type: ignore[arg-type]
    with pytest.raises(SecretsManifestError):
        SecretsManifest(entries=('TOPIC',))  # type: ignore[arg-type]


def test_manifest_path_type_is_validated() -> None:
    with pytest.raises(SecretsManifestError, match='path'):
        SecretsManifest.from_path(123)  # type: ignore[arg-type]


def test_manifest_rejects_reserved_environment_variable(tmp_path) -> None:
    entry = {
        'var_name': 'ENVIRONMENT',
        'secret_name': None,
        'value': 'dev',
        'exists_in_key_vault': False,
    }

    with pytest.raises(SecretsManifestError, match='ENVIRONMENT'):
        SecretsManifest.from_path(_write_manifest(tmp_path, [entry]))
