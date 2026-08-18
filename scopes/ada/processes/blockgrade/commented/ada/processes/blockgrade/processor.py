# Mantiene un alias de transición hacia el processor común.
from atlanticus.data_producers.sql import SqlDataProducerProcessor

BlockgradeSourceProcessor = SqlDataProducerProcessor

__all__ = ['BlockgradeSourceProcessor']
