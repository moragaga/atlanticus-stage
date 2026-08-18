class PiDataProducerError(RuntimeError):
    pass


class PiDataProducerCatalogError(PiDataProducerError):
    pass


class PiDataProducerWebIdRegistryError(PiDataProducerError):
    pass


class PiDataProducerWatermarkError(PiDataProducerError):
    pass


class PiDataProducerPlannerError(PiDataProducerError):
    pass


class PiDataProducerAcquisitionError(PiDataProducerError):
    pass


class PiDataProducerTimeoutExhaustedError(PiDataProducerAcquisitionError):
    def __init__(
        self,
        *,
        phase: str,
        retry_count: int,
        point_request_count: int = 0,
        interpolated_request_count: int = 0,
        recorded_request_count: int = 0,
        split_count: int = 0,
    ) -> None:
        self.phase = phase
        self.retry_count = retry_count
        self.point_request_count = point_request_count
        self.interpolated_request_count = interpolated_request_count
        self.recorded_request_count = recorded_request_count
        self.split_count = split_count
        super().__init__(f'PI Web API {phase} timeout persisted after {retry_count} retries')

    @property
    def request_count(self) -> int:
        return self.interpolated_request_count + self.recorded_request_count


class PiDataProducerMaterializationError(PiDataProducerError):
    pass
