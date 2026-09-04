# rag-prod

A small RAG chatbot built the way a production service is built, so the infrastructure
skills come from shipping rather than from tutorials.

Corpus is three SRE runbooks. Swap in your own markdown and nothing else changes.

## What's actually in here

| Layer | Choice | Why this one |
|---|---|---|
| API | FastAPI, async, SSE streaming | Streaming is table stakes; sync workers starve under it |
| Retrieval | pgvector HNSW + Postgres FTS, fused with RRF | Hybrid beats dense-only on keyword-ish queries |
| Embeddings | fastembed / bge-small (ONNX, CPU) | No torch in the image, ~90MB, baked in at build |
| LLM | any OpenAI-compatible endpoint | Ollama locally, LiteLLM or vLLM in cluster, no code change |
| Guardrail | refuse below a grounding threshold, forced citations | Cheapest hallucination control that exists |
| Metrics | Prometheus: TTFT, retrieval latency by retriever, refusal rate, empty-retrieval rate | These are the numbers you alert on |
| Traces | OpenTelemetry, span attributes carry chunk IDs | Bad answer to chunk ID in under a minute |
| Packaging | multi-stage Docker, non-root, weights baked | No weight download at pod start |
| Orchestration | Helm chart: probes, HPA, PDB, ServiceMonitor, topology spread | The parts that break real deploys |
| Delivery | Argo CD Application, GitOps | Cluster state comes from git |
| CI | ruff, schema, ingest, **eval gate**, image build, Trivy | A retrieval regression fails the build |

## Step 1: run it locally (15 minutes)

```bash
cp .env.example .env
ollama serve && ollama pull llama3.1:latest    # or point LLM_BASE_URL anywhere
make up                                         # postgres + api + prometheus + grafana + otel
make ingest                                     # idempotent; re-run is a no-op
curl -N -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"question":"why would the planner ignore my HNSW index?"}'
```

Then open Prometheus at :9090 and query `rag_time_to_first_token_seconds_bucket`.
Grafana is at :3000, anonymous admin, add Prometheus at `http://prometheus:9090`.

**Learn here:** SSE, connection pooling, probes, RRF, why liveness must not touch Postgres.

## Step 2: make the eval gate real

```bash
make eval        # scores recall@1, recall@5, MRR against eval/golden.jsonl
make baseline    # freeze current numbers
# now change CHUNK_CHARS in scripts/ingest.py, re-ingest, re-run eval
```

Break it on purpose. Set `CHUNK_CHARS = 200`, re-ingest, watch recall drop and the gate
fail. That failure is the single most convincing thing you can screenshot for an interview.

**Learn here:** why retrieval and generation are evaluated separately, why a prompt change
is a deploy-worthy event.

## Step 3: Kubernetes locally

```bash
brew install k3d helm            # macOS
make k3d                         # cluster + postgres + kube-prometheus-stack + Argo CD
make image && k3d image import rag-chatbot:dev -c rag
helm upgrade --install rag deploy/helm/rag-chatbot -n rag \
  --set image.repository=rag-chatbot --set image.tag=dev
kubectl -n rag get pods -w
```

Then break things deliberately:
- `kubectl -n data scale sts/postgres --replicas=0` and watch readiness fail while liveness holds
- `kubectl -n rag rollout restart deploy/rag-rag-chatbot` during an in-flight stream
- generate load with `make load` and watch the HPA react

**Learn here:** probes, rolling updates, PDBs, HPA behaviour, why graceful shutdown matters
when responses stream.

## Step 4: GitOps

```bash
# push this repo, edit repoURL in deploy/argocd/application.yaml
kubectl apply -f deploy/argocd/application.yaml
kubectl -n argocd port-forward svc/argocd-server 8081:443
```

Change `replicaCount` in values.yaml, commit, push, watch Argo CD sync it. Then change it
with `kubectl scale` and watch self-heal revert you. That loop is the whole point of GitOps.

## Step 5: observability that means something

Build one Grafana dashboard with exactly four panels:
`rag_time_to_first_token_seconds` p95, `rag_retrieval_duration_seconds` p95 by retriever,
`rate(rag_refusals_total[5m])`, `rate(rag_requests_total{outcome="error"}[5m])`.

Then one alert: p95 TTFT above 4s for 10 minutes. Route it to a Slack webhook.
Screenshot both. This is what "SLO-based alerting" means in practice.

## Step 6: cloud

Two paths, same image:
- **Fast:** `gcloud run deploy` or Render, plus Neon/Supabase for Postgres. Genuinely production for a solo product.
- **Resume-shaped:** Terraform an EKS or GKE cluster plus managed Postgres, point Argo CD at it, add Atlantis for PR-driven applies.

Do the fast path first so something is live, then do the Terraform path for the skill.

## Where to take it next

Turn `/chat` into an agent: give it tools (PromQL query, `kubectl get`, GitHub search),
run it through LangGraph with a Postgres checkpointer, add a dry-run mode and an approval
gate for anything mutating, then extend the eval set to score tool trajectories rather
than just retrieval. That is the on-call copilot, and it is the same repo three weekends later.
