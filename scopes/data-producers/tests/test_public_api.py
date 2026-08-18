import atlanticus.data_producers as data_producers


def test_public_api_exposes_current_sql_producer_contract() -> None:
    assert data_producers.__version__ == '0.1.0'
    assert callable(data_producers.build_sql_data_producer)
    assert data_producers.SqlLoadStrategy.SCOPED.value == 'scoped'
    assert data_producers.SqlStorageMode.PARTITIONED.value == 'partitioned'
