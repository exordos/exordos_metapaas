SHELL := bash
SSH_KEY    ?= ~/.ssh/id_ed25519.pub
REPOSITORY ?= http://10.20.0.1:8080/exordos-elements

all: help

help:
	@echo "build       - build the metapaas element image + manifest"
	@echo "install     - install the metapaas element into Core"
	@echo "lint        - run ruff check"
	@echo "format      - run ruff format"
	@echo "test        - run unit tests via tox"
	@echo "typecheck   - run mypy"

build:
	exordos build -c exordos/exordos.yaml -i $(SSH_KEY) -f \
		--manifest-var repository=$(REPOSITORY)

install:
	exordos em elements install output/manifests/metapaas.yaml

lint:
	tox -e ruff-check

format:
	tox -e ruff

test:
	tox -e py312

typecheck:
	tox -e mypy
