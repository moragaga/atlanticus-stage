# Los errores distinguen contratos inválidos de fallos al leer la fuente.


class RemanentesContractError(ValueError):
    pass


class RemanentesSourceError(RuntimeError):
    pass
