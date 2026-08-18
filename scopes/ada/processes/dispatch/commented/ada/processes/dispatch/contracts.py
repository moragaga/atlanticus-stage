# Mantiene un alias de transición hacia el contrato común del executor.
from atlanticus.data_producers.sql import SqlSourceExecutor

DispatchSourceExecutor = SqlSourceExecutor

__all__ = ['DispatchSourceExecutor']
