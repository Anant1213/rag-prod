# Runbook: vector search latency spike

## Symptoms
`rag_retrieval_duration_seconds{retriever="dense"}` p95 rises above 500ms while
lexical retrieval stays flat. Overall chat p95 breaches the SLO.

## Triage
1. Confirm the HNSW index is being used: `explain analyze` the dense query. A sequential
   scan means the planner rejected the index, usually because `ef_search` is unset or the
   table was recently bulk loaded without `analyze`.
2. Check table bloat after a large re-ingest. Deleting and reinserting every chunk leaves
   dead tuples that autovacuum may not have reclaimed yet.
3. Check whether the embedding dimension in the query matches the column definition.

## Remediation
- Run `analyze chunks;` after any bulk ingest.
- Raise `hnsw.ef_search` for recall or lower it for latency. It is a per-session GUC, so
  set it on the connection, not in postgresql.conf.
- If the index was built before a large ingest, rebuild it concurrently rather than
  dropping it: a dropped HNSW index means every query falls back to a full scan.

## Prevention
Build a new table, ingest into it, validate with the eval suite, then swap. Never reindex
in place on a table serving live traffic.
