package main

import (
	"migration-factory-plugin-mcp/internal/simulator"
	"strings"
	"testing"
)

// clearEnv unsets every variable loadConfig reads, so a test starts from a
// known environment whatever the developer has exported.
func clearEnv(t *testing.T) {
	t.Helper()
	for _, name := range []string{envBaseURL, envAPIKey, envDefaultAPIKey, envWorkspaceID, envGroupID,
		envFirecrawlBaseURL, envFirecrawlAPIKey, envDefaultFirecrawlAPIKey} {
		t.Setenv(name, "")
	}
}

func TestLoadConfigReadsTheEnvironment(t *testing.T) {
	clearEnv(t)
	t.Setenv(envBaseURL, "  mw.simulator.company  ")
	t.Setenv(envAPIKey, "key-1")

	cfg, err := loadConfig(nil)
	if err != nil {
		t.Fatalf("loadConfig: %v", err)
	}
	// The values arrive trimmed — an env var pasted from a shell history
	// routinely carries trailing space, and it would end up in a URL.
	if cfg.BaseURL != "mw.simulator.company" {
		t.Errorf("BaseURL = %q, want the trimmed host", cfg.BaseURL)
	}
	if cfg.APIKey != "key-1" {
		t.Errorf("APIKey = %q, want it read straight from the env", cfg.APIKey)
	}

	// The bare host is normalised into a gateway URL on the way into the
	// client (client() hands BaseURL to simulator.New, which normalises).
	if got := simulator.NormalizeBaseURL(cfg.BaseURL); got != "https://mw.simulator.company/papi/1.0" {
		t.Errorf("normalised BaseURL = %q, want the gateway", got)
	}
}

func TestLoadConfigNamesTheMissingVariable(t *testing.T) {
	clearEnv(t)
	t.Setenv(envBaseURL, "mw.simulator.company")
	t.Setenv(envAPIKey, "")

	_, err := loadConfig(nil)
	if err == nil {
		t.Fatal("loadConfig succeeded without a credential")
	}
	// A fresh install fails here more often than anywhere else, so the
	// message has to say which variable to set.
	if !strings.Contains(err.Error(), envAPIKey) {
		t.Errorf("error %q does not name %s", err, envAPIKey)
	}
}

// An unset gateway is not a failure: it is the client's own DefaultBaseURL,
// the same split loadFirecrawlConfig has. A portable Agent Plugins v1 mcp.json
// cannot write "${SIM_BASE_URL:-…}" — its env values are literal — so a host
// that starts this server from one supplies no gateway at all, and an install
// that supplies none has to reach the cloud rather than refuse to start.
func TestLoadConfigLeavesAnAbsentGatewayToTheClient(t *testing.T) {
	tests := []struct {
		name    string
		baseURL string
	}{
		{"unset", ""},
		// A client that does not expand "${SIM_BASE_URL:-…}" hands the text
		// over as the gateway; it is a missing value, not a host to dial.
		{"placeholder", "${SIM_BASE_URL:-https://mw.simulator.company/papi/1.0}"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			clearEnv(t)
			t.Setenv(envBaseURL, tc.baseURL)
			t.Setenv(envAPIKey, "key-1")

			cfg, err := loadConfig(nil)
			if err != nil {
				t.Fatalf("loadConfig: %v", err)
			}
			if cfg.BaseURL != "" {
				t.Errorf("BaseURL = %q, want it left to the client's default", cfg.BaseURL)
			}
			if got := simulator.NormalizeBaseURL(cfg.BaseURL); got != "" {
				t.Errorf("normalised BaseURL = %q, want the client to fill it in", got)
			}
		})
	}
}

// A pinned DEFAULT_SIM_API_KEY is what a deployer's install runs on, and
// a caller that has a real key — mf-api, starting the cc-api project with the
// migration session's own key — displaces it by exporting SIM_API_KEY.
func TestLoadConfigFallsBackToTheDefaultKey(t *testing.T) {
	tests := []struct {
		name       string
		apiKey     string
		defaultKey string
		want       string
	}{
		{"caller key wins", "key-caller", "key-pinned", "key-caller"},
		{"default when unset", "", "key-pinned", "key-pinned"},
		// An unset ${VAR} without a default reaches the process verbatim; it
		// is a missing value, not a key, and must not beat the fallback.
		{"placeholder is not a key", "${SIM_API_KEY}", "key-pinned", "key-pinned"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			clearEnv(t)
			t.Setenv(envBaseURL, "mw.simulator.company")
			t.Setenv(envAPIKey, tc.apiKey)
			t.Setenv(envDefaultAPIKey, tc.defaultKey)

			cfg, err := loadConfig(nil)
			if err != nil {
				t.Fatalf("loadConfig: %v", err)
			}
			if cfg.APIKey != tc.want {
				t.Errorf("APIKey = %q, want %q", cfg.APIKey, tc.want)
			}
		})
	}
}

// The group is optional and never fatal: a run mf-api did not start has no
// session group, and a value that is not a group id must not become one.
func TestLoadConfigReadsTheGroup(t *testing.T) {
	tests := []struct {
		name  string
		value string
		want  int
	}{
		{"set", "4242", 4242},
		{"trimmed", "  4242 ", 4242},
		{"unset", "", 0},
		{"garbage", "dto-group-abc", 0},
		{"zero", "0", 0},
		{"negative", "-7", 0},
		{"placeholder", "${SIM_GROUP_ID}", 0},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			clearEnv(t)
			t.Setenv(envBaseURL, "mw.simulator.company")
			t.Setenv(envAPIKey, "key-1")
			t.Setenv(envGroupID, tc.value)

			cfg, err := loadConfig(nil)
			if err != nil {
				t.Fatalf("loadConfig: %v", err)
			}
			if cfg.GroupID != tc.want {
				t.Errorf("GroupID = %d, want %d", cfg.GroupID, tc.want)
			}
		})
	}
}

func TestLoadFirecrawlConfigFallsBackToThePinnedKey(t *testing.T) {
	// Same split as the Simulator pair: a caller with a key of its own sets
	// FIRECRAWL_API_KEY in the process environment, and the pinned default
	// keeps an unconfigured install able to read a page.
	clearEnv(t)
	t.Setenv(envDefaultFirecrawlAPIKey, "fc-pinned")

	cfg, err := loadFirecrawlConfig()
	if err != nil {
		t.Fatalf("loadFirecrawlConfig: %v", err)
	}
	if cfg.APIKey != "fc-pinned" {
		t.Errorf("APIKey = %q, want the pinned fallback", cfg.APIKey)
	}
	// No instance configured is not an error: the client uses the shared one.
	if cfg.BaseURL != "" {
		t.Errorf("BaseURL = %q, want it left to the client's default", cfg.BaseURL)
	}

	t.Setenv(envFirecrawlAPIKey, "fc-own")
	cfg, err = loadFirecrawlConfig()
	if err != nil {
		t.Fatalf("loadFirecrawlConfig: %v", err)
	}
	if cfg.APIKey != "fc-own" {
		t.Errorf("APIKey = %q, want the caller's own key to win", cfg.APIKey)
	}
}

func TestLoadFirecrawlConfigNamesTheMissingKey(t *testing.T) {
	clearEnv(t)
	// A client that does not expand "${FIRECRAWL_API_KEY:-…}" hands the text
	// over as the key; it is a missing value, not a credential to send.
	t.Setenv(envDefaultFirecrawlAPIKey, "${FIRECRAWL_API_KEY:-fc-pinned}")

	_, err := loadFirecrawlConfig()
	if err == nil {
		t.Fatal("loadFirecrawlConfig accepted an unexpanded placeholder as a key")
	}
	if !strings.Contains(err.Error(), envFirecrawlAPIKey) {
		t.Errorf("error %q does not name %s", err, envFirecrawlAPIKey)
	}
}

// A host that starts this server once and shares it between runs — Hermes
// keeps one process per profile — has no other way to say whose layer a call
// writes. Every field the call names wins over the environment; the ones it
// leaves out keep the server's own, so an install that names only a key still
// talks to the gateway it was configured with.
func TestACallCanNameTheWorkspaceItWritesTo(t *testing.T) {
	clearEnv(t)
	t.Setenv(envBaseURL, "mw.simulator.company")
	t.Setenv(envAPIKey, "server-key")
	t.Setenv(envWorkspaceID, "server-ws")
	t.Setenv(envGroupID, "111")

	cfg, err := loadConfig(&simOverride{
		BaseURL: "sim.simulator.company", APIKey: "session-key", WorkspaceID: "session-ws", GroupID: 222,
	})
	if err != nil {
		t.Fatalf("loadConfig: %v", err)
	}
	if cfg.APIKey != "session-key" || cfg.BaseURL != "sim.simulator.company" ||
		cfg.WorkspaceID != "session-ws" || cfg.GroupID != 222 {
		t.Fatalf("cfg = %+v, want every field from the call", cfg)
	}

	// Named in part: the gateway and the group stay the server's.
	partial, err := loadConfig(&simOverride{APIKey: "session-key"})
	if err != nil {
		t.Fatalf("loadConfig: %v", err)
	}
	if partial.APIKey != "session-key" || partial.BaseURL != "mw.simulator.company" ||
		partial.WorkspaceID != "server-ws" || partial.GroupID != 111 {
		t.Fatalf("cfg = %+v, want only the key replaced", partial)
	}
}

// The property the whole scheme rests on: ten sessions building at once share
// this process, so what one call names must not reach the next. Nothing is
// stored — each call is read afresh — and this is what says so.
func TestWhatOneCallNamesDoesNotReachTheNext(t *testing.T) {
	clearEnv(t)
	t.Setenv(envBaseURL, "mw.simulator.company")
	t.Setenv(envAPIKey, "server-key")

	for _, key := range []string{"session-a", "session-b", "session-c"} {
		cfg, err := loadConfig(&simOverride{APIKey: key})
		if err != nil {
			t.Fatalf("loadConfig: %v", err)
		}
		if cfg.APIKey != key {
			t.Fatalf("APIKey = %q, want %q", cfg.APIKey, key)
		}
	}
	back, err := loadConfig(nil)
	if err != nil {
		t.Fatalf("loadConfig: %v", err)
	}
	if back.APIKey != "server-key" {
		t.Errorf("APIKey = %q — a previous call's key outlived it", back.APIKey)
	}
}

// A caller that expands nothing hands over "${SIM_API_KEY}" verbatim; dialling
// that would be worse than falling back.
func TestAnUnexpandedPlaceholderInACallIsNotAKey(t *testing.T) {
	clearEnv(t)
	t.Setenv(envAPIKey, "server-key")
	cfg, err := loadConfig(&simOverride{APIKey: "${SIM_API_KEY}", BaseURL: "${SIM_BASE_URL}"})
	if err != nil {
		t.Fatalf("loadConfig: %v", err)
	}
	if cfg.APIKey != "server-key" {
		t.Errorf("APIKey = %q, want the environment's", cfg.APIKey)
	}
}

// With no key anywhere the message must name both ways of giving one: a
// service that passes them per call reads "set it in the env" as a dead end.
func TestMissingKeyNamesBothWaysToGiveOne(t *testing.T) {
	clearEnv(t)
	_, err := loadConfig(nil)
	if err == nil {
		t.Fatal("want an error")
	}
	for _, want := range []string{"sim.api_key", envAPIKey} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error %q does not mention %q", err, want)
		}
	}
}
