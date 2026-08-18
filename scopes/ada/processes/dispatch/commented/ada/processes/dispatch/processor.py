# Mantiene un alias de transición hacia el processor común.
from atlanticus.data_producers.sql import SqlDataProducerProcessor

DispatchSourceProcessor = SqlDataProducerProcessor

__all__ = ['DispatchSourceProcessor']
