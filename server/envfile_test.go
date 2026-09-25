package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestParseEnvFileReadsWhatPeopleWrite(t *testing.T) {
	const file = `
# a comment, and the blank line above it
SIM_API_KEY=key-1
export SIM_WORKSPACE_ID=42
  SIM_GROUP_ID = 7
FIRECRAWL_API_KEY="fc 1"
DEFAULT_SIM_API_KEY='fc-2'
# a secret is not a reference: the $ stays where it was written
ODD=a${NOT_EXPANDED}b
`
	values, err := parseEnvFile(strings.NewReader(file))
	if err != nil {
		t.Fatalf("parseEnvFile: %v", err)
	}
	want := map[string]string{
		"SIM_API_KEY":         "key-1",
		"SIM_WORKSPACE_ID":    "42",
		"SIM_GROUP_ID":        "7",
		"FIRECRAWL_API_KEY":   "fc 1",
		"DEFAULT_SIM_API_KEY": "fc-2",
		"ODD":                 "a${NOT_EXPANDED}b",
	}
	if len(values) != len(want) {
		t.Errorf("read %d variable(s), want %d: %v", len(values), len(want), values)
	}
	for name, value := range want {
		if values[name] != value {
			t.Errorf("%s = %q, want %q", name, values[name], value)
		}
	}
}

func TestParseEnvFileRefusesALineThatIsNotOne(t *testing.T) {
	// A file that is not a .env at all — a pasted key on its own, say — is
	// worth a message naming the line rather than a silent half-load.
	if _, err := parseEnvFile(strings.NewReader("SIM_API_KEY=key-1\njust-a-key\n")); err == nil {
		t.Fatal("parseEnvFile accepted a line without a name")
	} else if !strings.Contains(err.Error(), "line 2") {
		t.Errorf("error %q does not name the line", err)
	}
}

func TestApplyEnvFileOnlyFillsGaps(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".env")
	if err := os.WriteFile(path, []byte("SIM_API_KEY=from-file\nSIM_BASE_URL=from-file\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	// The caller that has a real key — mf-api — sets it in the environment,
	// and a file left over from an earlier install must not displace it.
	t.Setenv(envAPIKey, "from-the-environment")
	// A variable spelled out with nothing in it is a gap, not a value.
	t.Setenv(envBaseURL, "")

	applied, err := applyEnvFile(path)
	if err != nil {
		t.Fatalf("applyEnvFile: %v", err)
	}
	if applied != 1 {
		t.Errorf("applied %d variable(s), want only the empty one", applied)
	}
	if got := os.Getenv(envAPIKey); got != "from-the-environment" {
		t.Errorf("%s = %q, want the environment to win", envAPIKey, got)
	}
	if got := os.Getenv(envBaseURL); got != "from-file" {
		t.Errorf("%s = %q, want the file to fill the gap", envBaseURL, got)
	}
}

func TestEnvFilePathPrefersTheOneNamedOutright(t *testing.T) {
	t.Setenv(envEnvFile, "/somewhere/keys.env")
	t.Setenv(envPluginData, "/data")

	path, explicit := envFilePath()
	if path != "/somewhere/keys.env" || !explicit {
		t.Errorf("envFilePath() = %q, %v; want the named file", path, explicit)
	}

	// Left to the host: the data directory a portable Agent Plugins v1 host
	// hands over is where the file conventionally lives.
	t.Setenv(envEnvFile, "")
	path, explicit = envFilePath()
	if want := filepath.Join("/data", ".env"); path != want || explicit {
		t.Errorf("envFilePath() = %q, %v; want %q as a convention", path, explicit, want)
	}

	// Neither: an ordinary Claude Code install, which passes the environment
	// through and has no file to read.
	t.Setenv(envPluginData, "")
	if path, _ = envFilePath(); path != "" {
		t.Errorf("envFilePath() = %q, want nothing to read", path)
	}
}

// The whole point, end to end: a key that reaches a filtered-environment host
// only through the file still reaches loadConfig.
func TestLoadEnvFileCarriesTheKeyIntoTheConfig(t *testing.T) {
	clearEnv(t)
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, ".env"), []byte("SIM_API_KEY=key-from-plugin-data\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv(envEnvFile, "")
	t.Setenv(envPluginData, dir)

	loadEnvFile()

	cfg, err := loadConfig()
	if err != nil {
		t.Fatalf("loadConfig: %v", err)
	}
	if cfg.APIKey != "key-from-plugin-data" {
		t.Errorf("APIKey = %q, want the one the file carried", cfg.APIKey)
	}
}

// An install with no file is the usual one, and it says nothing and fails at
// nothing: loadEnvFile is called on every start, including Claude Code's.
func TestLoadEnvFileIsSilentWithoutAFile(t *testing.T) {
	t.Setenv(envEnvFile, "")
	t.Setenv(envPluginData, t.TempDir())

	loadEnvFile()
}
