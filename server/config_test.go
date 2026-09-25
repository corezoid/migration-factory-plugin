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

	cfg, err := loadConfig()
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
	tests := []struct {
		name    string
		baseURL string
		apiKey  string
		want    string
	}{
		{"no key", "mw.simulator.company", "", envAPIKey},
		{"no gateway", "", "key-1", envBaseURL},
		// A client that does not expand "${SIM_BASE_URL:-…}" hands the text
		// over as the gateway; it is a missing value, not a host to dial.
		{"gateway placeholder", "${SIM_BASE_URL:-https://mw.simulator.company/papi/1.0}", "key-1", envBaseURL},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			clearEnv(t)
			t.Setenv(envBaseURL, tc.baseURL)
			t.Setenv(envAPIKey, tc.apiKey)

			_, err := loadConfig()
			if err == nil {
				t.Fatal("loadConfig succeeded without a credential")
			}
			// A fresh install fails here more often than anywhere else, so
			// the message has to say which variable to set.
			if !strings.Contains(err.Error(), tc.want) {
				t.Errorf("error %q does not name %s", err, tc.want)
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

			cfg, err := loadConfig()
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

			cfg, err := loadConfig()
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
