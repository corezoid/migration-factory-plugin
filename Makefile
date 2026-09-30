# migration-factory-plugin — install/refresh into a Hermes profile's own
# plugin registry (not the personal Skill Hub — see README.md "Install" →
# "Hermes Agent"). `server/Makefile` builds and tests the Go module; this one
# only ships the already-committed package into a running Hermes.

# --- Hermes: install/refresh the plugin in one profile -----------------------
#
# Two registries, and mf-api's runs need both.
#
# A portable Agent Plugins v1 package installs disabled and does nothing until
# enabled (README.md:476): `plugins enable` is what starts the MCP server —
# confirmed live, a raw initialize+tools/list handshake against the installed
# launch-mcp answers with all five tools and full schemas. But the *skills* it
# registers land under a namespaced id, `agent-plugin-migration-factory-plugin-
# <hash>/dto-fill` — confirmed too: `hermes -s dto-fill` (bare) answers
# "Unknown skill(s): dto-fill", `hermes -s agent-plugin-migration-factory-
# plugin-7ec05b64/dto-fill` resolves and starts a real run. mf-api
# (internal/build/hermes.go, hermesCommand) sends the bare name — the one
# each SKILL.md documents as its own call, `/dto-fill` — because that is what
# the personal Skill Hub registers under, measured live 2026-09-27 against a
# Hub install. A plugin-only install therefore starts a working MCP server
# that no bare `/dto-fill` from mf-api can ever reach: the skill half has to
# go into the Hub too.
#
# The Hub, in turn, does not take a local path — `skills install <local dir>`
# answers "Could not download" regardless of the exact path form (bare, or
# `file://`), confirmed live 2026-09-28. It only takes an identifier it can
# fetch: `<owner>/<repo>/skills/<skill>` resolves through skills.sh's mirror of
# this public repo, runs its security scan (verdict SAFE, logged), and
# installs under the bare name. That path hits the *unauthenticated* GitHub
# API — 60 requests/hour, shared by everything on the pod — and six skills
# across two profiles burns it fast; `HERMES_GITHUB_ENV`, a `KEY=VALUE` file
# **already sitting on the pod** (never a Makefile variable — a token belongs
# in a file `kubectl cp` places there, not in a command line a `ps aux` on the
# pod would show to every other engineer with access to it), raises the quota
# to 5,000/hour when set.
#
# Hub installs are per-profile despite a shared download cache: a profile
# that never installed a given skill still needs `--force`, or the CLI reports
# "already installed" (true of the cache, not of this profile) and leaves its
# Hub empty — confirmed live when `migration-factory` kept answering "Unknown
# skill(s)" after an unforced install that printed no error at all.
#
# `--force` on the plugin install makes a repeat call safe: it removes and
# reinstalls rather than refusing because the name exists. `--no-enable` then
# `enable --no-allow-tool-override` is the two-step README.md documents; the
# package overrides no built-in tool (its five are new names), so there is
# nothing to grant.
#
#   make hermes-deploy                                                     # profile migration-factory, pod hermes-0
#   make hermes-deploy HERMES_PROFILE=default
#   make hermes-deploy HERMES_GITHUB_ENV=/opt/data/.env-github             # raises the GitHub API quota; see above
#   make hermes-deploy HERMES_PROFILE=other HERMES_NAMESPACE=other-ns
#
HERMES_NAMESPACE     ?= hermes-dev
HERMES_POD           ?= hermes-0
HERMES_CONTAINER     ?= hermes
HERMES_BIN           ?= /opt/hermes/bin/hermes
HERMES_PROFILE       ?= migration-factory
HERMES_PLUGIN_NAME   ?= migration-factory-plugin
HERMES_PLUGIN_SOURCE ?= corezoid/migration-factory-plugin
# A file already on the pod, `KEY=VALUE` per line — e.g. GITHUB_TOKEN=…  —
# sourced before every Hub install. Empty by default: unauthenticated GitHub
# API calls work, just capped at 60/hour.
HERMES_GITHUB_ENV    ?=
HERMES_SKILLS        := dto-fill dto-fill-lite dto-fill-loop dto-fill-via-web dto-fill-via-web-lite bank-statement-to-jsonl bank-statement-to-dto

.PHONY: hermes-deploy
hermes-deploy: ## Install/refresh this plugin (MCP server + Hub skills) in a Hermes profile (HERMES_PROFILE=… HERMES_NAMESPACE=… HERMES_GITHUB_ENV=…)
	@set -eu; \
	command -v kubectl >/dev/null 2>&1 || { echo "hermes-deploy: kubectl не найден"; exit 1; }; \
	ns='$(HERMES_NAMESPACE)'; pod='$(HERMES_POD)'; ctr='$(HERMES_CONTAINER)'; \
	bin='$(HERMES_BIN)'; profile='$(HERMES_PROFILE)'; name='$(HERMES_PLUGIN_NAME)'; \
	hx() { kubectl -n "$$ns" exec "$$pod" -c "$$ctr" -- "$$bin" -p "$$profile" "$$@"; }; \
	echo "hermes-deploy: [$$profile] installing $$name ← $(HERMES_PLUGIN_SOURCE) (MCP server)"; \
	hx plugins install '$(HERMES_PLUGIN_SOURCE)' --force --no-enable; \
	hx plugins enable "$$name" --no-allow-tool-override; \
	for s in $(HERMES_SKILLS); do \
	  echo "hermes-deploy: [$$profile] Hub skill $$s ← $(HERMES_PLUGIN_SOURCE)/skills/$$s"; \
	  kubectl -n "$$ns" exec "$$pod" -c "$$ctr" -- sh -c \
	    '$(if $(HERMES_GITHUB_ENV),set -a; . '"$(HERMES_GITHUB_ENV)"' 2>/dev/null; set +a;) '"$$bin"' -p '"$$profile"' skills install "$(HERMES_PLUGIN_SOURCE)/skills/'"$$s"'" --yes --force'; \
	done; \
	echo "hermes-deploy: [$$profile] done — verifying every skill resolves by bare name in one session:"; \
	joined=$$(echo $(HERMES_SKILLS) | tr ' ' ','); \
	hx --skills "$$joined" -z "Say only: all loaded"

# --- Hermes: push the local working tree directly, no git involved ---------
#
# hermes-deploy (above) only ever sees what's pushed to $(HERMES_PLUGIN_SOURCE)
# on GitHub — useless while iterating on uncommitted local changes. This
# target instead tars the local tree and `kubectl cp`s it straight onto the
# pod, into every physical location that actually matters:
#
#   - the two plugin-registry copies (MCP server + its bundled skills/):
#     /opt/data/plugins/$(HERMES_PLUGIN_NAME) and
#     /opt/data/profiles/$(HERMES_PROFILE)/plugins/$(HERMES_PLUGIN_NAME) —
#     confirmed live to be two independent directories, not a symlink pair;
#   - the two Skill Hub copies (bare `/dto-fill` etc. resolve here, not
#     against the plugin's own skills/ — see the note on hermes-deploy above):
#     /opt/data/skills/<skill> (default profile) and
#     /opt/data/profiles/$(HERMES_PROFILE)/skills/<skill>. Only the
#     HERMES_SKILLS subdirectories are touched inside those — both Hub roots
#     hold plenty of other people's skills alongside these six.
#
# No backups: this repo's git history is the safety net. Nothing is ever
# left behind as a sibling inside a directory Hermes itself scans, either —
# earlier this target did leave `.pre-sync-<ts>` copies beside the live
# skills/plugins, and the Skill Hub resolves a bare name by walking every
# subdirectory of its root and matching on the skill's own name, so a
# `dto-fill-lite.pre-sync-<ts>/SKILL.md` sitting next to the real
# `dto-fill-lite/` answered to that same name — confirmed live, it turned
# "dto-fill-lite" ambiguous until the stray copy was removed. This version
# never creates one.
#
# The tarball is built with COPYFILE_DISABLE=1 so a macOS client's tar does
# not litter the pod with AppleDouble `._*` resource-fork files — it already
# did once before this existed; clean-up also runs defensively after
# extraction in case some other client's tar re-adds them.
#
# Overwriting the files is NOT enough by itself, and this bit long: Hermes'
# gateway spawns this plugin's MCP server ONCE per (profile) and keeps that
# one child process alive across every session afterward — confirmed live
# 2026-09-29 via `ps -ef` on the pod, a `python3 -m migration_factory_plugin_mcp`
# child of `hermes gateway run --replace`, still running from the day before
# with the old schema loaded into its interpreter, surviving both a fresh
# `hermes-sync` (files updated, process untouched) AND a `plugins disable` +
# `plugins enable` cycle (the CLI itself says "deferred: MCP servers (next
# session)", but a `-z` one-shot session still hit the same stale PID). Only
# killing that one process forced the gateway to lazily spawn a fresh one —
# proved by re-running the same one-shot session, which then saw the new
# tool schema for real, through the actual `tool_call` path a live session
# uses, not just a hand-spawned `launch-mcp` handshake. So this target kills
# it directly, by the one string that names it and nothing else: overwriting
# files gets the new code onto disk, and this is what gets it into a process
# that is actually being asked to run it.
#
#   make hermes-sync
#   make hermes-sync HERMES_PROFILE=default
#
.PHONY: hermes-sync
hermes-sync: ## Push the local tree (MCP server + skills) onto Hermes directly, no git, no backups (HERMES_PROFILE=… HERMES_NAMESPACE=…)
	@set -eu; \
	command -v kubectl >/dev/null 2>&1 || { echo "hermes-sync: kubectl не найден"; exit 1; }; \
	ns='$(HERMES_NAMESPACE)'; pod='$(HERMES_POD)'; ctr='$(HERMES_CONTAINER)'; \
	profile='$(HERMES_PROFILE)'; name='$(HERMES_PLUGIN_NAME)'; \
	ts=$$(date +%Y%m%d-%H%M%S); \
	kx() { kubectl -n "$$ns" exec "$$pod" -c "$$ctr" -- sh -c "$$1"; }; \
	plugin_tar=/tmp/mfp-plugin-$$ts.tar.gz; skills_tar=/tmp/mfp-skills-$$ts.tar.gz; \
	echo "hermes-sync: packing the plugin tree (server + vendor + launch-mcp + manifests)…"; \
	COPYFILE_DISABLE=1 tar --exclude='.git' --exclude='session-run_*.json' --exclude='.idea' \
		--exclude='.claude' --exclude='Makefile' --exclude='server/.venv' --exclude='server/.pytest_cache' \
		--exclude='server/*.egg-info' --exclude='__pycache__' \
		-czf "$$plugin_tar" .claude-plugin .mcp.json README.md launch-mcp mcp.json plugin.json server skills vendor; \
	echo "hermes-sync: packing skills/ alone for the Hub copies…"; \
	COPYFILE_DISABLE=1 tar -czf "$$skills_tar" $(foreach s,$(HERMES_SKILLS),skills/$(s)); \
	kubectl -n "$$ns" cp "$$plugin_tar" "$$pod:/tmp/mfp-plugin-$$ts.tar.gz" -c "$$ctr"; \
	kubectl -n "$$ns" cp "$$skills_tar" "$$pod:/tmp/mfp-skills-$$ts.tar.gz" -c "$$ctr"; \
	rm -f "$$plugin_tar" "$$skills_tar"; \
	for d in /opt/data/plugins/$$name /opt/data/profiles/$$profile/plugins/$$name; do \
		echo "hermes-sync: [$$profile] plugin ← $$d"; \
		kx "test -d $$d || { echo '  skip: no such directory on the pod'; exit 0; }; \
			rm -rf $$d/server $$d/bin; \
			tar -xzf /tmp/mfp-plugin-$$ts.tar.gz -C $$d; \
			chmod +x $$d/launch-mcp; \
			find $$d -name '._*' -delete"; \
	done; \
	for d in /opt/data/skills /opt/data/profiles/$$profile/skills; do \
		echo "hermes-sync: [$$profile] Hub ← $$d"; \
		kx "test -d $$d || { echo '  skip: no such directory on the pod'; exit 0; }; \
			mkdir -p /tmp/mfp-skills-extract-$$ts && tar -xzf /tmp/mfp-skills-$$ts.tar.gz -C /tmp/mfp-skills-extract-$$ts; \
			for s in $(HERMES_SKILLS); do \
				rm -rf $$d/\$$s; \
				cp -a /tmp/mfp-skills-extract-$$ts/skills/\$$s $$d/\$$s; \
			done; \
			rm -rf /tmp/mfp-skills-extract-$$ts; \
			find $$d -maxdepth 2 -name '._*' -delete"; \
	done; \
	kx "rm -f /tmp/mfp-plugin-$$ts.tar.gz /tmp/mfp-skills-$$ts.tar.gz"; \
	echo "hermes-sync: [$$profile] restarting the gateway's pooled MCP server process, so a live session actually runs the code just synced (see the note above this target — a file overwrite alone never reaches it):"; \
	kx "if pkill -f 'migration_factory_plugin_mcp'; then echo '  killed the running server; the gateway will spawn a fresh one on next use'; else echo '  no running server process found (fine on a first sync, or if nothing has used this plugin yet)'; fi"; \
	echo "hermes-sync: [$$profile] verifying the deployed launch-mcp answers a raw handshake:"; \
	kx "cd /opt/data/profiles/$$profile/plugins/$$name && printf '%s\n%s\n%s\n' \
		'{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},\"clientInfo\":{\"name\":\"hermes-sync\",\"version\":\"0\"}}}' \
		'{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\"}' \
		'{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\"}' \
		| timeout 8 ./launch-mcp 2>&1 | grep -o '\"name\":\"[a-z_]*\"' | sort -u"; \
	echo "hermes-sync: [$$profile] verifying a Hub skill still resolves by bare name:"; \
	kx ". /opt/hermes/.venv/bin/activate 2>/dev/null; hermes -p $$profile --skills dto-fill-lite -z 'Say only: loaded ok' 2>&1 | tail -3"; \
	echo "hermes-sync: done."

.PHONY: help
help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-16s %s\n", $$1, $$2}'
