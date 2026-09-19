"""
Excepciones de dominio.

El dominio lanza estas excepciones sin saber que terminarán
convirtiéndose en un código HTTP — esa traducción vive en
app/api/exception_handlers.py. Esto evita que la lógica de negocio
se contamine con detalles de transporte (¿esto es un 404 o un 409?),
y permite reusar el mismo caso de uso desde otro transporte
(gRPC, CLI, un job batch) sin cambiar una línea del dominio.
"""


class DomainError(Exception):
    """Clase base para todos los errores de negocio."""


class AccountNotFoundError(DomainError):
    def __init__(self, account_id):
        super().__init__(f"Cuenta no encontrada: {account_id}")
        self.account_id = account_id


class InsufficientFundsError(DomainError):
    def __init__(self, account_id, balance, amount):
        super().__init__(f"Saldo insuficiente en cuenta {account_id}: disponible={balance}, solicitado={amount}")
        self.account_id = account_id
        self.balance = balance
        self.amount = amount


class ConcurrencyConflictError(DomainError):
    """Se agotaron los reintentos de locking optimista."""

    def __init__(self, account_id):
        super().__init__(f"Conflicto de concurrencia irresoluble en cuenta {account_id}")
        self.account_id = account_id
