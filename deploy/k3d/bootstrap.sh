#!/usr/bin/env bash
# One-shot local cluster: k3d + Postgres/pgvector + kube-prometheus-stack + Argo CD.
set -euo pipefail

CLUSTER=${CLUSTER:-rag}

if k3d cluster list -o json | grep -q "\"name\":\"$CLUSTER\""; then
  echo "cluster '$CLUSTER' already exists, reusing it"
else
  k3d cluster create "$CLUSTER" \
    --agents 2 \
    --port "8080:80@loadbalancer" \
    --port "9091:30090@server:0" \
    --k3s-arg "--disable=traefik@server:*"
fi

kubectl create namespace rag        --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace data       --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace observability --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace argocd     --dry-run=client -o yaml | kubectl apply -f -

# --- Postgres with pgvector ---
# Plain manifests rather than the Bitnami chart: see deploy/k3d/postgres.yaml.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
kubectl create configmap postgres-initdb -n data \
  --from-file=init.sql="$HERE/../../scripts/init_db.sql" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f "$HERE/postgres.yaml"
kubectl -n data rollout status sts/postgres --timeout=300s

# --- Prometheus + Grafana + Alertmanager (brings the ServiceMonitor CRD) ---
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
helm upgrade --install kps prometheus-community/kube-prometheus-stack -n observability \
  --set grafana.service.type=NodePort \
  --set grafana.service.nodePort=30090 \
  --set grafana.adminPassword=admin \
  --wait

# --- Argo CD ---
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl -n argocd rollout status deploy/argocd-server --timeout=300s

echo
echo "Grafana:  http://localhost:9091  (admin / admin)"
echo "Argo CD:  kubectl -n argocd port-forward svc/argocd-server 8081:443"
echo "  password: kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"
