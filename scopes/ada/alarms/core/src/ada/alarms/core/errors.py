class AlarmCoreError(Exception):
    pass


class AlarmContractError(AlarmCoreError):
    pass


class AlarmLifecycleError(AlarmCoreError):
    pass
