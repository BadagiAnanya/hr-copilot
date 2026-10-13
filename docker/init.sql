-- Enable extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Documents table (parent of chunks)
CREATE TABLE IF NOT EXISTS documents (
    id              TEXT PRIMARY KEY,            -- e.g. 'leave_policy_v3'
    title           TEXT NOT NULL,
    source_url      TEXT,
    version         INT NOT NULL DEFAULT 1,
    effective_date  DATE,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    content_hash    TEXT NOT NULL
);

-- Chunks: the retrieval unit
CREATE TABLE IF NOT EXISTS chunks (
    id              BIGSERIAL PRIMARY KEY,
    document_id     TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    section         TEXT,                        -- heading breadcrumb, e.g. "3.1 Sick Leave"
    content         TEXT NOT NULL,
    embedding       vector(384),                 -- bge-small-en-v1.5 dim
    tsv             tsvector,                    -- BM25 via Postgres FTS
    acl_groups      TEXT[] NOT NULL DEFAULT '{all_employees}',
    department      TEXT,
    effective_date  DATE,
    version         INT NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON chunks USING GIN(tsv);
CREATE INDEX IF NOT EXISTS idx_chunks_acl ON chunks USING GIN(acl_groups);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);

-- Auto-maintain tsvector on insert/update
CREATE OR REPLACE FUNCTION chunks_tsv_trigger() RETURNS trigger AS $$
BEGIN
    NEW.tsv := to_tsvector('english', coalesce(NEW.section, '') || ' ' || coalesce(NEW.content, ''));
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_chunks_tsv ON chunks;
CREATE TRIGGER trg_chunks_tsv
BEFORE INSERT OR UPDATE OF content, section ON chunks
FOR EACH ROW EXECUTE FUNCTION chunks_tsv_trigger();

-- Mock HRMS: employees
CREATE TABLE IF NOT EXISTS employees (
    employee_id     TEXT PRIMARY KEY,
    slack_user_id   TEXT UNIQUE,
    name            TEXT NOT NULL,
    email           TEXT NOT NULL,
    department      TEXT NOT NULL,
    location        TEXT NOT NULL,
    manager_id      TEXT,
    joined_at       DATE NOT NULL,
    acl_groups      TEXT[] NOT NULL DEFAULT '{all_employees}'
);

-- Mock HRMS: leave balances
CREATE TABLE IF NOT EXISTS leave_balances (
    employee_id     TEXT NOT NULL REFERENCES employees(employee_id),
    year            INT NOT NULL,
    leave_type      TEXT NOT NULL,    -- 'sick' | 'casual' | 'earned'
    allotted        INT NOT NULL,
    used            INT NOT NULL DEFAULT 0,
    PRIMARY KEY (employee_id, year, leave_type)
);

-- Audit log (append-only)
CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGSERIAL PRIMARY KEY,
    trace_id        TEXT NOT NULL,
    slack_user_id   TEXT,
    employee_id     TEXT,
    question        TEXT NOT NULL,
    answer          TEXT,
    retrieved_ids   BIGINT[],
    citations       JSONB,
    tool_calls      JSONB,
    route           TEXT,
    latency_ms      INT,
    feedback        TEXT,  -- 'up' | 'down' | null
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(slack_user_id);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(created_at DESC);
