# Fachada de compatibilidad hacia el productor NOT PII global.
from atlanticus.data_producers.notpii._wide import build_wide, empty_wide

__all__ = ['build_wide', 'empty_wide']
