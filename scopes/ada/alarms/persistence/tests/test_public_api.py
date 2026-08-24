import ada.alarms.persistence as persistence


def test_public_api_exports_stable_persistence_contract() -> None:
    assert persistence.__version__ == '0.1.0'
    assert persistence.ENGINE_COMMIT_RECORD_SCHEMA_VERSION == 'engine-commit-record.v1'
    assert persistence.GROUP_RUNTIME_SNAPSHOT_SCHEMA_VERSION == 'group-runtime-snapshot.v1'
    assert persistence.JOURNAL_HEAD_SCHEMA_VERSION == 'journal-head.v1'
    assert persistence.AlarmPersistence.__name__ == 'AlarmPersistence'
    assert persistence.EngineCommitRecord.__name__ == 'EngineCommitRecord'
    assert persistence.GroupRuntimeSnapshot.__name__ == 'GroupRuntimeSnapshot'
    assert persistence.JournalHead.__name__ == 'JournalHead'
    assert persistence.MutationFence is not None
