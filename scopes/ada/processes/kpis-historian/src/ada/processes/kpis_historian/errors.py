class KpiHistorianError(Exception):
    pass


class KpiHistorianConfigurationError(KpiHistorianError):
    pass


class KpiHistorianWatermarkError(KpiHistorianError):
    pass


class KpiHistorianHistoryError(KpiHistorianError):
    pass
