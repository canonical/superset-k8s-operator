# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

# Variables for paths and configuration

PROJECT_ROOT := $(CURDIR)

# Shell strict mode
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

METADATA_YAML := $(PROJECT_ROOT)/metadata.yaml
ROCK_DIR := $(PROJECT_ROOT)/superset_rock
ROCKCRAFT_YAML := $(ROCK_DIR)/rockcraft.yaml

REGISTRY := localhost:32000

# Ensure yq is installed: 'sudo snap install yq'
CHARM_NAME := $(shell yq '.name' $(METADATA_YAML))
CHARM_ARCH := ubuntu-22.04-amd64

ROCK_NAME := $(shell yq '.name' $(ROCKCRAFT_YAML))
ROCK_VERSION := $(shell yq '.version' $(ROCKCRAFT_YAML))
ROCK_ARCH := amd64

# Tag for local deployment to bypass caching
LOCAL_ROCK_TAG := $(ROCK_VERSION)-$(shell date +%s)

# The expected output files from charmcraft/rockcraft pack
CHARM_FILE := $(PROJECT_ROOT)/$(CHARM_NAME)_$(CHARM_ARCH).charm
ROCK_FILE := $(ROCK_DIR)/$(ROCK_NAME)_$(ROCK_VERSION)_$(ROCK_ARCH).rock

# Default target
.PHONY: help
help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  build             Build both charm and rock"
	@echo "  build-charm       Build the charm using charmcraft"
	@echo "  build-rock        Build the OCI archive (rock) using rockcraft"
	@echo "  check-build-deps  Check if necessary dependencies for building are installed"
	@echo "  check-deploy-deps Check if necessary dependencies for deploying are installed"
	@echo "  install-build-deps Install dependencies needed for building"
	@echo "  install-deploy-deps Install dependencies needed for deploying"
	@echo "  checks            Run all the code quality checks"
	@echo "  clean             Remove built charm and rock files"
	@echo "  clean-charmcraft  Clean charmcraft environment"
	@echo "  clean-rockcraft   Clean rockcraft environment"
	@echo "  deploy-local      Deploy charm with local resources"
	@echo "  fmt               Apply coding style standards to code"
	@echo "  import-rock       Build and import the rock into MicroK8s"
	@echo "  lint              Check code against coding style standards"
	@echo "  test              Run unit and static tests"
	@echo "  test-integration  Run integration tests"
	@echo "  test-static       Run static type checks"
	@echo "  test-unit         Run unit tests"
	@echo "  help              Show this help message"
	@echo "  venv              Create a virtual environment"

.PHONY: build
build: build-charm build-rock

.PHONY: install-build-deps
install-build-deps:
	@echo "Installing build dependencies..."
	sudo snap install yq
	sudo snap install astral-uv --channel latest/stable --classic
	sudo snap install charmcraft --channel latest/stable --classic
	sudo snap install rockcraft --channel latest/stable --classic
	sudo snap alias rockcraft.skopeo skopeo
	uv tool install tox --with tox-uv
	@echo "All build dependencies installed."

.PHONY: install-deploy-deps
install-deploy-deps:
	@echo "Installing deployment dependencies..."
	sudo snap install juju --channel 3/stable
	sudo snap install microk8s --channel 1.34-strict/stable
	sudo snap install docker --channel latest/stable
	@echo "All deployment dependencies installed."

.PHONY: check-build-deps
check-build-deps:
	@which yq >/dev/null || (echo "yq not found" && exit 1)
	@which uv >/dev/null || (echo "uv not found" && exit 1)
	@which charmcraft >/dev/null || (echo "charmcraft not found" && exit 1)
	@which rockcraft >/dev/null || (echo "rockcraft not found" && exit 1)
	@which tox >/dev/null || (echo "tox not found" && exit 1)
	@echo "All build dependencies are installed."

.PHONY: check-deploy-deps
check-deploy-deps:
	@which juju >/dev/null || (echo "juju not found" && exit 1)
	@which docker >/dev/null || (echo "docker not found" && exit 1)
	@which microk8s >/dev/null || (echo "microk8s not found" && exit 1)
	@which skopeo >/dev/null || (echo "skopeo not found" && exit 1)
	@echo "All deployment dependencies are installed."

.PHONY: checks
checks: fmt lint test

.PHONY: clean
clean:
	@echo "Cleaning up..."
	rm -f $(PROJECT_ROOT)/*.charm
	rm -f $(ROCK_DIR)/*.rock

.PHONY: clean-charmcraft
clean-charmcraft:
	@echo "Cleaning charmcraft environment..."
	cd $(PROJECT_ROOT) && charmcraft clean

.PHONY: clean-rockcraft
clean-rockcraft:
	@echo "Cleaning rockcraft environment..."
	cd $(ROCK_DIR) && rockcraft clean

.PHONY: deploy-local
deploy-local:
	@echo "Deploying charm with local resources..."
	juju deploy $(CHARM_FILE) --resource superset-image=$(REGISTRY)/$(ROCK_NAME):latest superset-k8s-ui

.PHONY: fmt
fmt:
	tox -e fmt

.PHONY: lint
lint:
	tox -e lint

.PHONY: test
test: test-unit test-static

.PHONY: test-integration
test-integration:
	tox -e integration

.PHONY: test-static
test-static:
	tox -e static

.PHONY: test-unit
test-unit:
	tox -e unit

.PHONY: build-charm
build-charm:
	@echo "Building charm..."
	cd $(PROJECT_ROOT) && charmcraft pack --use-lxd --verbose

# Build the rock only if rockcraft.yaml changes or the file is missing
$(ROCK_FILE): $(ROCKCRAFT_YAML)
	@echo "Building rock..."
	cd $(ROCK_DIR) && rockcraft pack --use-lxd --verbose

.PHONY: build-rock
build-rock: $(ROCK_FILE)

# import-rock depends on the rock file
.PHONY: import-rock
import-rock: $(ROCK_FILE)
	@echo "Importing rock into MicroK8s registry..."
	skopeo --insecure-policy copy --dest-tls-verify=false \
		oci-archive:$(ROCK_FILE) \
		docker://$(REGISTRY)/$(ROCK_NAME):$(LOCAL_ROCK_TAG)
	skopeo --insecure-policy copy --dest-tls-verify=false \
		oci-archive:$(ROCK_FILE) \
		docker://$(REGISTRY)/$(ROCK_NAME):latest

.PHONY: venv
venv:
	uv venv --clear venv
	uv pip install --python venv/bin/python -r requirements.txt
