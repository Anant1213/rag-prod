# Runbook: Postgres connection pool exhaustion

## Symptoms
API returns 503 with "database unavailable". `rag_ready` gauge flaps between 0 and 1.
`pg_stat_activity` count approaches `max_connections`.

## Triage
1. Check active connections: `select count(*), state from pg_stat_activity group by state;`
2. Look for connections stuck in `idle in transaction` for more than 60 seconds. That is
   almost always an application bug: a code path that opens a transaction and awaits an
   LLM call inside it.
3. Check whether a deploy rolled out in the last 15 minutes. A pod count increase multiplies
   pool size by replica count.

## Remediation
- Short term: terminate idle-in-transaction backends older than 5 minutes with
  `pg_terminate_backend`. This is safe; those transactions are not doing work.
- Medium term: lower `max_size` in the connection pool so that `replicas * max_size` stays
  under 70 percent of `max_connections`.
- Long term: put PgBouncer in transaction pooling mode in front of Postgres.

## Prevention
Never hold a database transaction open across a network call to a model provider.
