-- 0001: vectors/chunk text (BM25 corpus source), document registry, chat sessions. ADR-0003.
-- Every table carries tenant_id and owner_id; every query filters on both.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS vector_chunks (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    doc_id text NOT NULL,
    chunk_id text NOT NULL,
    text text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(384) NOT NULL,
    PRIMARY KEY (tenant_id, owner_id, doc_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS vector_chunks_embedding_hnsw
    ON vector_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS documents (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    doc_id text NOT NULL,
    filename text NOT NULL,
    mime text NOT NULL,
    size bigint NOT NULL CHECK (size >= 0),
    sha256 text NOT NULL,
    pages_or_sheets integer NOT NULL DEFAULT 0 CHECK (pages_or_sheets >= 0),
    created_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('pending','processing','ready','failed')),
    failure_reason text,
    PRIMARY KEY (tenant_id, doc_id),
    UNIQUE (tenant_id, owner_id, sha256)
);
CREATE INDEX IF NOT EXISTS ix_documents_owner ON documents (tenant_id, owner_id, created_at);

CREATE TABLE IF NOT EXISTS sessions (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    session_id text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, session_id)
);
CREATE INDEX IF NOT EXISTS ix_sessions_owner ON sessions (tenant_id, owner_id);
CREATE INDEX IF NOT EXISTS ix_sessions_expires ON sessions (expires_at);

CREATE TABLE IF NOT EXISTS session_messages (
    id bigserial PRIMARY KEY,
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    session_id text NOT NULL,
    role text NOT NULL CHECK (role IN ('user','assistant')),
    content text NOT NULL,
    citation_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL,
    FOREIGN KEY (tenant_id, session_id) REFERENCES sessions (tenant_id, session_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_session_messages_sid ON session_messages (tenant_id, session_id, id);
