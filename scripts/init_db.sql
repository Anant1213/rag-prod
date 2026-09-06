create extension if not exists vector;

create table if not exists chunks (
    chunk_id    bigserial primary key,
    tenant      text not null default 'default',
    doc_id      text not null,
    source      text not null,
    version     text not null default 'v1',      -- commit sha / etag: lineage
    chunk_index int  not null,
    text        text not null,
    page        int,                             -- 1-based source page for PDFs, null for markdown
    embedding   vector(384) not null,
    tsv         tsvector generated always as (to_tsvector('english', text)) stored,
    created_at  timestamptz not null default now(),
    unique (tenant, doc_id, chunk_index)
);

-- Additive migration for databases created before `page` existed, so this file
-- stays runnable against both a fresh and an already-seeded cluster.
alter table chunks add column if not exists page int;

-- dense index. m/ef_construction are the two knobs you will be asked about.
create index if not exists chunks_embedding_hnsw
    on chunks using hnsw (embedding vector_cosine_ops)
    with (m = 16, ef_construction = 64);

create index if not exists chunks_tsv_gin on chunks using gin (tsv);
create index if not exists chunks_tenant_doc on chunks (tenant, doc_id);

-- tenant isolation at the database, not in application code
alter table chunks enable row level security;
