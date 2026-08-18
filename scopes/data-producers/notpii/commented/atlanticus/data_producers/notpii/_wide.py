# Normaliza los registros largos de NOT PII a una tabla ancha por timestamp.
from __future__ import annotations

import pandas as pd

from atlanticus.data_producers.notpii.errors import NotPiiSourceError


def build_wide(
    *,
    dataframe: pd.DataFrame,
    aliases_by_tag_name: dict[str, str],
    expected_aliases: tuple[str, ...],
) -> pd.DataFrame:
    required = {'timestamp', 'id_tag', 'valor'}
    missing = required - set(dataframe.columns)
    if missing:
        raise NotPiiSourceError(
            'NotPII data is missing required columns: ' + ', '.join(sorted(missing))
        )
    if dataframe.empty:
        return empty_wide(expected_aliases)
    working = dataframe.copy()
    working['_timestamp_utc'] = pd.to_datetime(working['timestamp'], utc=True, errors='coerce')
    working['_tag_name'] = working['id_tag'].astype('string').str.strip().str.upper()
    working['_alias'] = working['_tag_name'].map(aliases_by_tag_name)
    working = working.dropna(subset=['_timestamp_utc', '_alias'])
    if working.empty:
        return empty_wide(expected_aliases)
    if 'timestamp_ingesta' in working.columns:
        working['_source_order'] = pd.to_datetime(
            working['timestamp_ingesta'], utc=True, errors='coerce'
        ).fillna(working['_timestamp_utc'])
    else:
        working['_source_order'] = range(len(working))
    working = working.sort_values(['_timestamp_utc', '_alias', '_source_order'], kind='stable')
    working = working.drop_duplicates(subset=['_timestamp_utc', '_alias'], keep='last')
    try:
        wide = working.pivot(index='_timestamp_utc', columns='_alias', values='valor')
    except ValueError as error:
        raise NotPiiSourceError('NotPII data could not be normalized') from error
    wide = wide.reindex(columns=list(expected_aliases))
    wide.columns.name = None
    return wide.reset_index(names='timestamp_utc')


def empty_wide(expected_aliases: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            'timestamp_utc': pd.Series(dtype='datetime64[ns, UTC]'),
            **{alias: pd.Series(dtype='object') for alias in expected_aliases},
        }
    )
