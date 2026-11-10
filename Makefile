SHELL := /bin/bash
COMPOSE := docker compose

.PHONY: help build download up down init sync describe embed search status run

help:
	@echo "make build     build the pipeline image (needs BUILD_HTTP_PROXY)"
	@echo "make download  download Qwen3-Embedding-8B Q8_0 (resumable)"
	@echo "make up        start llama.cpp embedding server"
	@echo "make init      create role/schema/tables in the Immich Postgres"
	@echo "make sync      mirror asset metadata from the Immich API"
	@echo "make describe  generate descriptions for pending assets"
	@echo "make embed     embed described assets"
	@echo "make run       sync + describe + embed in a loop"
	@echo "make status    pipeline counters"
	@echo "make search Q='陶笛'"

build:
	$(COMPOSE) build

download:
	$(COMPOSE) --profile setup run --rm model-download

up:
	$(COMPOSE) up -d llama-embed

down:
	$(COMPOSE) down

init:
	$(COMPOSE) run --rm pipeline init-db

sync:
	$(COMPOSE) run --rm pipeline sync

describe:
	$(COMPOSE) run --rm pipeline describe --limit $(or $(LIMIT),128)

embed:
	$(COMPOSE) run --rm pipeline embed --limit $(or $(LIMIT),2048)

run:
	$(COMPOSE) run --rm pipeline run --chunk $(or $(CHUNK),64)

status:
	$(COMPOSE) run --rm pipeline status

search:
	$(COMPOSE) run --rm pipeline search "$(Q)" -k $(or $(K),10)

