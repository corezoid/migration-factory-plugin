.DEFAULT_GOAL := check

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: venv
venv:
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e .
	$(PIP) install -q pytest ruff

.PHONY: check
check: format-check lint test

.PHONY: format
format:
	$(VENV)/bin/ruff format migration_factory_plugin_mcp tests

.PHONY: format-check
format-check:
	$(VENV)/bin/ruff format --check migration_factory_plugin_mcp tests

.PHONY: lint
lint:
	$(VENV)/bin/ruff check migration_factory_plugin_mcp tests

.PHONY: test
test:
	$(PYTHON) -m pytest tests/unit -q

.PHONY: clean
clean:
	rm -rf $(VENV) vendor .pytest_cache
	find . -name '__pycache__' -exec rm -rf {} +

# --- vendor: wheel bundles for a host with no pip/network (Hermes) ----------
#
# Mirrors the Go version's cross-compiled bin/ binaries: one bundle per
# platform, committed at the repo root (../vendor/<platform>/) so
# ../launch-mcp works with nothing but a bare python3. Rebuild and commit
# whenever a dependency changes. Versions are pinned to exactly what `make
# venv` resolves locally (see requirements-vendor.txt) — never a bare
# unpinned name, which would silently pick up whatever is newest at build
# time instead of what was actually tested.
VENDOR_PY_TAG := cp313
# One tag per manylinux glibc baseline pip should try, oldest first — a
# wheel built for an old baseline runs fine on a newer glibc host (Debian 13
# trixie, here), so trying several just maximizes the chance every package
# has a matching wheel published.
VENDOR_MANYLINUX_TAGS := manylinux2014 manylinux_2_17 manylinux_2_28 manylinux_2_31 manylinux_2_34 manylinux_2_39
VENDOR_PLATFORMS := x86_64:linux-amd64 aarch64:linux-arm64

.PHONY: vendor
vendor:
	@for pair in $(VENDOR_PLATFORMS); do \
		arch=$${pair%%:*}; dir=$${pair##*:}; \
		echo "vendoring for $$dir ($$arch, $(VENDOR_PY_TAG))..."; \
		rm -rf ../vendor/$$dir; \
		mkdir -p ../vendor/$$dir; \
		platform_flags=""; \
		for tag in $(VENDOR_MANYLINUX_TAGS); do platform_flags="$$platform_flags --platform $${tag}_$$arch"; done; \
		$(PIP) download --no-deps --only-binary=:all: --python-version $(VENDOR_PY_TAG) \
			--implementation cp --abi $(VENDOR_PY_TAG) $$platform_flags \
			-d /tmp/mfp-vendor-$$dir -r requirements-vendor.txt; \
		for whl in /tmp/mfp-vendor-$$dir/*.whl; do \
			$(PYTHON) -m zipfile -e "$$whl" ../vendor/$$dir/; \
		done; \
		find ../vendor/$$dir -name RECORD -delete; \
		rm -rf /tmp/mfp-vendor-$$dir; \
	done

# --- live: opt-in tests against a real Simulator/Firecrawl, never in CI ------

.PHONY: live-export
live-export:
	SIM_LIVE=1 $(PYTHON) -m pytest tests/live/test_live.py::test_live_export_graph -q -s

.PHONY: live-apply
live-apply:
	SIM_LIVE=1 $(PYTHON) -m pytest tests/live/test_live.py::test_live_apply_ops -q -s

.PHONY: live-find
live-find:
	SIM_LIVE=1 $(PYTHON) -m pytest tests/live/test_live.py::test_live_find_records -q -s

.PHONY: live-read
live-read:
	SIM_LIVE=1 $(PYTHON) -m pytest tests/live/test_live.py::test_live_read_page -q -s

.PHONY: live-post
live-post:
	SIM_LIVE=1 $(PYTHON) -m pytest tests/live/test_live.py::test_live_post_statement_batch -q -s

.PHONY: mcp-handshake
mcp-handshake:
	@printf '%s\n%s\n' \
		'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
		'{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
		| ../launch-mcp

.PHONY: help
help:
	@echo "venv           create .venv and install the package + dev tools"
	@echo "check          format-check + lint + test (the default target)"
	@echo "format          apply ruff formatting"
	@echo "test            run the unit test suite"
	@echo "vendor          rebuild the vendored wheel bundles under vendor/"
	@echo "live-export/apply/find/read/post   opt-in tests against a real workspace"
	@echo "mcp-handshake   smoke-test the stdio protocol through ../launch-mcp"
	@echo "clean           remove .venv, vendor/, and caches"
