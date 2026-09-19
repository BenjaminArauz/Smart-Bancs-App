-- Script DDL/DML — SmartBancs App
-- Se ejecuta automáticamente la primera vez que arranca el contenedor
-- de Postgres (montado en /docker-entrypoint-initdb.d/).
--
-- Nota de diseño: `status` se guarda como VARCHAR con CHECK en vez de
-- un tipo ENUM nativo de Postgres. Esto evita el trabajo extra de
-- gestionar migraciones de tipos ENUM (agregar un valor nuevo a un
-- ENUM nativo requiere ALTER TYPE, más engorroso que un CHECK) y
-- coincide con cómo está mapeado en el ORM (native_enum=False).

CREATE EXTENSION IF NOT EXISTS "pgcrypto"; -- para gen_random_uuid()

CREATE TABLE IF NOT EXISTS accounts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    balance         NUMERIC(18, 2) NOT NULL CHECK (balance >= 0),
    version         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key     VARCHAR(255) NOT NULL UNIQUE,
    account_id          UUID NOT NULL REFERENCES accounts(id),
    amount              NUMERIC(18, 2) NOT NULL CHECK (amount > 0),
    status              VARCHAR(20) NOT NULL CHECK (status IN ('completed')),
    trace_id            VARCHAR(64) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Índice de soporte para las búsquedas de idempotencia y de historial por cuenta
CREATE INDEX IF NOT EXISTS idx_transactions_account_id ON transactions(account_id);

CREATE TABLE IF NOT EXISTS outbox_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      VARCHAR(100) NOT NULL,
    payload         JSONB NOT NULL,
    trace_id        VARCHAR(64) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ NULL
);

-- Índice de soporte para el polling del worker de outbox (busca solo lo no procesado)
CREATE INDEX IF NOT EXISTS idx_outbox_unprocessed ON outbox_events(created_at) WHERE processed_at IS NULL;

-- Resultado del scoring de IA (punto 3.3), escrito por el worker AIRelayWorker
-- al consumir eventos outbox de tipo 'ai.recommend'.
CREATE TABLE IF NOT EXISTS risk_scores (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id  UUID NOT NULL UNIQUE REFERENCES transactions(id),
    risk_score      NUMERIC(5, 4) NOT NULL CHECK (risk_score >= 0 AND risk_score <= 1),
    risk_level      VARCHAR(20) NOT NULL CHECK (risk_level IN ('low', 'medium', 'high')),
    model_version   VARCHAR(50) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- El worker relay (pendiente) consulta esto para saber qué le falta publicar
CREATE INDEX IF NOT EXISTS idx_outbox_unprocessed ON outbox_events(created_at) WHERE processed_at IS NULL;

-- Datos semilla: una cuenta de prueba para poder probar el endpoint de inmediato
INSERT INTO accounts (id, balance, version)
VALUES ('11111111-1111-1111-1111-111111111111', 1000.00, 0)
ON CONFLICT (id) DO NOTHING;