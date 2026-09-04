# Runbook: model provider outage or timeout storm

## Symptoms
`rag_requests_total{route="chat",outcome="error"}` climbs. Time to first token histogram
empties out. Retrieval metrics stay healthy, which localises the fault to the model plane.

## Triage
1. Check the provider status page and your gateway logs before touching the cluster.
   Retrieval being healthy means the problem is downstream of your code.
2. Distinguish timeouts from rate limits. A 429 storm means a noisy tenant or a runaway
   retry loop, not a provider outage.
3. Check whether a prompt change increased context length enough to push latency past the
   client timeout.

## Remediation
- Flip the gateway to the fallback model. With LiteLLM this is a config change, not a deploy.
- If it is a rate limit, apply a per-tenant limit at the gateway and let the offending
  tenant degrade rather than the whole service.
- Disable retries temporarily if retries are amplifying the outage.

## Prevention
Every model call needs a timeout shorter than the client timeout, capped retries with
jitter, and a circuit breaker. Serve a cached or refused answer rather than hanging.
