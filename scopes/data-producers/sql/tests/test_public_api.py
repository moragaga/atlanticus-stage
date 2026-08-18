import atlanticus.data_producers.sql as sql


def test_public_api_exposes_current_sql_producer_contract() -> None:
    assert sql.__version__ == '0.1.0'
    assert callable(sql.build_sql_data_producer)
    assert sql.SqlLoadStrategy.SCOPED.value == 'scoped'
    assert sql.SqlStorageMode.PARTITIONED.value == 'partitioned'
