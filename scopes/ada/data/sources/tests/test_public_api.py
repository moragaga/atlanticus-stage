import ada.data.sources as sources


def test_public_api() -> None:
    assert sources.__version__ == '0.1.0'
    assert sources.DataSourceLoader is not None
    assert sources.DataPartitionBinding is not None
    assert sources.DataSourceRegistry is not None
    assert sources.LoadedDataSourceView is not None
    assert sources.PandasRuntimeFrameContext is not None
    assert sources.OperationalWindowResolver is not None
