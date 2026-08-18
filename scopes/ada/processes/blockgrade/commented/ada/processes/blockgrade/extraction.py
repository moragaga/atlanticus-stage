# Mantiene un alias de transición hacia el reader SQL común.
from atlanticus.data_producers.sql import SqlDataProducerReader, build_select

BlockgradeSqlReader = SqlDataProducerReader
_build_select = build_select

__all__ = ['BlockgradeSqlReader', '_build_select']
