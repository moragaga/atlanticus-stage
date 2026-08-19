class KpiPlannerError(Exception):
    pass


class KpiPlanKeyError(KpiPlannerError, KeyError):
    pass
