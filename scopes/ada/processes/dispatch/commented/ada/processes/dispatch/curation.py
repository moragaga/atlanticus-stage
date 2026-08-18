# Mantiene aliases de transición hacia la curación común.
from atlanticus.data_producers.sql import curate_table, source_last_update_utc

curate_dispatch_table = curate_table

__all__ = ['curate_dispatch_table', 'source_last_update_utc']
