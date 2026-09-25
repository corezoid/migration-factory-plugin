# migration-factory-plugin-mcp — the MCP server behind the dto-fill skill.
#
# Everything it needs is in the environment, and it is two pairs:
# SIM_BASE_URL and SIM_API_KEY for the layer, FIRECRAWL_BASE_URL and
# FIRECRAWL_API_KEY for the page reader. A .env beside this Makefile is loaded
# when it exists, so the live targets work without exporting anything.

BINARY  := migration-factory-plugin-mcp
BIN_DIR := ../bin
OUT     := $(BIN_DIR)/$(BINARY)

ifneq (,$(wildcard .env))
include .env
export
endif

.DEFAULT_GOAL := check

# A binary for this machine, under the unsuffixed name ../launch-mcp falls back
# to. A machine with a Go toolchain does not need it — the launcher builds from
# source when there is no binary for its platform — so this is for running the
# server somewhere by hand.
.PHONY: build
build: ## compile a standalone binary into ../bin (optional)
	@mkdir -p $(BIN_DIR)
	go build -trimpath -ldflags "-s -w" -o $(OUT) .
	@echo "built $(OUT)"

# The binaries that ship with the plugin. A host with no Go toolchain — a
# container, typically, and the Hermes image is one — can start the server only
# from these, so they are committed and ../launch-mcp picks the one matching
# `uname`. Rebuild and commit them whenever the server changes.
RELEASE_PLATFORMS := linux/amd64 linux/arm64

.PHONY: release
release: ## cross-compile the committed binaries into ../bin (one per shipped platform)
	@mkdir -p $(BIN_DIR)
	@for platform in $(RELEASE_PLATFORMS); do \
		goos=$${platform%%/*}; goarch=$${platform##*/}; \
		out=$(BIN_DIR)/$(BINARY)-$$goos-$$goarch; \
		CGO_ENABLED=0 GOOS=$$goos GOARCH=$$goarch \
			go build -trimpath -ldflags "-s -w" -o $$out . || exit 1; \
		echo "built $$out"; \
	done

.PHONY: test
test: ## unit tests, no network
	go test ./...

.PHONY: check
check: ## what CI would run
	gofmt -l . | tee /dev/stderr | (! read)
	go vet ./...
	go test ./...

.PHONY: tidy
tidy:
	go mod tidy

.PHONY: clean
clean:
	rm -f $(OUT)

# ----------------------------------------------------------------- live runs
# These reach a real gateway. They are opt-in and skip without a credential.

LAYER ?=
OUT_DIR ?=
OPS ?=
WRITE ?=

.PHONY: live-export
live-export: ## export a layer: make live-export LAYER=<uuid> OUT_DIR=./out
	@test -n "$(LAYER)" || { echo "LAYER=<uuid> is required"; exit 2; }
	SIM_LIVE=1 SIM_LAYER=$(LAYER) $(if $(OUT_DIR),SIM_OUT_DIR=$(OUT_DIR)) \
		go test . -run TestLiveExportGraph -v -count=1

.PHONY: live-apply
live-apply: ## plan an ops file: make live-apply OPS=./out/graph.ops.yaml [WRITE=1]
	@test -n "$(OPS)" || { echo "OPS=<graph.ops.yaml> is required"; exit 2; }
	SIM_LIVE=1 SIM_OPS=$(OPS) $(if $(LAYER),SIM_LAYER=$(LAYER)) $(if $(WRITE),SIM_WRITE=1) \
		go test . -run TestLiveApplyOps -v -count=1 -timeout 15m

TYPE ?=
VALUES ?=
FIELDS ?=

.PHONY: live-find
live-find: ## check identities against a form: make live-find TYPE=<slug> OUT_DIR=./out VALUES=a,b [FIELDS=iban,title]
	@test -n "$(TYPE)" || { echo "TYPE=<slug> is required"; exit 2; }
	@test -n "$(OUT_DIR)" || { echo "OUT_DIR=<dir with types.schema.yaml> is required"; exit 2; }
	@test -n "$(VALUES)" || { echo "VALUES=<comma-separated identities> is required"; exit 2; }
	SIM_LIVE=1 SIM_TYPE=$(TYPE) SIM_OUT_DIR=$(OUT_DIR) SIM_VALUES='$(VALUES)' $(if $(FIELDS),SIM_FIELDS=$(FIELDS)) \
		go test . -run TestLiveFindRecords -v -count=1

URL ?=

IMAGES ?=

.PHONY: live-read
live-read: ## read one page: make live-read URL=https://example.com/about [IMAGES=0]
	@test -n "$(URL)" || { echo "URL=<address> is required"; exit 2; }
	SIM_LIVE=1 FIRECRAWL_URL=$(URL) $(if $(IMAGES),FIRECRAWL_IMAGES=$(IMAGES)) \
		go test . -run TestLiveReadPage -v -count=1

.PHONY: mcp-handshake
mcp-handshake: ## smoke-test the stdio protocol through the launcher, no network
	@printf '%s\n%s\n' \
		'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
		'{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | ../launch-mcp

.PHONY: help
help:
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-16s %s\n", $$1, $$2}'
