.PHONY: up down schema ingest eval baseline dev image k3d deploy load

up:            ## local stack: postgres, api, prometheus, grafana, otel
	docker compose up -d --build

down:
	docker compose down -v

schema:
	psql "$${DATABASE_URL:-postgresql://rag:rag@localhost:5432/rag}" -f scripts/init_db.sql

ingest:
	python -m scripts.ingest docs/

eval:
	python -m eval.run_eval

baseline:
	python -m eval.run_eval --write

dev:
	uvicorn app.main:app --reload --port 8000

image:
	docker build -t rag-chatbot:dev .

k3d:
	./deploy/k3d/bootstrap.sh

deploy:
	helm upgrade --install rag deploy/helm/rag-chatbot -n rag --create-namespace

load:
	hey -n 200 -c 10 -m POST -H "Content-Type: application/json" \
	  -d '{"question":"why would the planner ignore my HNSW index?"}' \
	  http://localhost:8000/search
