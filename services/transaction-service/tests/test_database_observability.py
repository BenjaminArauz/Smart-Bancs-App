from app.infrastructure.database import _error_type


class PostgresError(Exception):
    def __init__(self, sqlstate):
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def test_classifies_postgres_incident_errors():
    assert _error_type(PostgresError("40P01")) == "deadlock"
    assert _error_type(PostgresError("55P03")) == "lock_timeout"
    assert _error_type(PostgresError("57014")) == "statement_timeout"


def test_classifies_connection_pool_timeout():
    assert _error_type(TimeoutError()) == "pool_timeout"