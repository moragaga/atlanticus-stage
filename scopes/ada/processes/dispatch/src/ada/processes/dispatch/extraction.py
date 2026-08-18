from atlanticus.data_producers.sql import SqlDataProducerReader, build_select

DispatchSqlReader = SqlDataProducerReader
_build_select = build_select

__all__ = ['DispatchSqlReader', '_build_select']
