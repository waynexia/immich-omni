SHELL := /bin/bash
COMPOSE := docker compose

.PHONY: help build download llama up down init sync describe embed search status run worker web logs

help:
	@echo "make build     build the pipeline image (needs BUILD_HTTP_PROXY)"
	@echo "make download  download Qwen3-Embedding-8B Q8_0 (resumable)"
	@echo "make llama     build llama-server on the host and the runtime image"
	@echo "make up        start llama.cpp, gateway, worker and web UI"
	@echo "make worker    start only the long-running worker"
	@echo "make web       start only the progress UI (port 10313)"
	@echo "make logs      follow worker logs"
	@echo "make init      create role/schema/tables in the Immich Postgres"
	@echo "make sync      mirror asset metadata from the Immich API"
	@echo "make describe  generate descriptions for pending assets"
	@echo "make embed     embed described assets"
	@echo "make run       sync + describe + embed in a loop"
	@echo "make status    pipeline counters"
	@echo "make search Q='陶笛'"

build:
	$(COMPOSE) build pipeline

download:
	$(COMPOSE) --profile setup run --rm model-download

llama:
	bash scripts/build_llama.sh
	$(COMPOSE) build llama-embed

up:
	$(COMPOSE) up -d llama-embed model-gateway worker web

worker:
	$(COMPOSE) up -d worker

web:
	$(COMPOSE) up -d web

logs:
	$(COMPOSE) logs -f worker

down:
	$(COMPOSE) down

init:
	$(COMPOSE) run --rm pipeline init-db

sync:
	$(COMPOSE) run --rm pipeline sync $(if $(LIMIT),--limit $(LIMIT),)

describe:
	$(COMPOSE) run --rm pipeline describe --limit $(or $(LIMIT),128)

embed:
	$(COMPOSE) run --rm pipeline embed --limit $(or $(LIMIT),2048)

run:
	$(COMPOSE) run --rm pipeline run --chunk $(or $(CHUNK),64) --max-rounds $(or $(ROUNDS),1000)

status:
	$(COMPOSE) run --rm pipeline status

search:
	$(COMPOSE) run --rm pipeline search "$(Q)" -k $(or $(K),10)

