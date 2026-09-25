package main

import (
	"bufio"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"strings"
)

// A portable Agent Plugins v1 host — Hermes — starts this server with an
// environment of its own making: a fixed safe list (PATH, HOME, TMPDIR, …)
// plus whatever the package's own mcp.json spells out, and nothing else. That
// leaves a workspace API key with no way in. It cannot be exported into the
// shell, because the shell's variables are filtered out before the process
// starts; and it cannot be written into mcp.json, because that file ships with
// the package and is public by construction — the format's own specification
// says so.
//
// What does survive is PLUGIN_DATA: the writable directory the host gives a
// package for its own state. So that is where the credentials go — a .env file
// the user writes once. Claude Code needs none of this: it passes the
// environment through, and an install whose variables are already set never
// reads a file.
//
// Nothing here expands, interpolates or executes: a line is a name and a
// value. The process environment always wins, so a file can only fill a gap,
// never displace what the caller deliberately set — which keeps mf-api, whose
// keys arrive in the environment, ahead of anything left on disk.
const (
	// envEnvFile names the file outright, for an install that keeps its
	// credentials somewhere the host does not know about. Set, unreadable is
	// worth a line in the log: somebody meant it to be read.
	envEnvFile = "MIGRATION_FACTORY_PLUGIN_ENV_FILE"
	// envPluginData is the per-package writable directory an Agent Plugins v1
	// host hands over (under Hermes: ~/.hermes/plugin-data/<namespace>).
	// Nothing else sets it, which is why an absent file here is silence
	// rather than a warning — most installs have no .env and want none.
	envPluginData = "PLUGIN_DATA"
)

// envFileName is what is read inside PLUGIN_DATA.
const envFileName = ".env"

// loadEnvFile fills the gaps in the environment from a .env file, and is
// called once, before the first tool call. Both ways of finding the file are
// tried in order of how deliberate they are: the variable that names one
// outright, then the host's own data directory.
func loadEnvFile() {
	path, explicit := envFilePath()
	if path == "" {
		return
	}
	applied, err := applyEnvFile(path)
	switch {
	case err != nil && (explicit || !os.IsNotExist(err)):
		// An absent file at the conventional location is the usual case and
		// says nothing; one that was named, or one that exists and cannot be
		// read, is a mistake worth seeing.
		log.Printf("%s: %v", path, err)
	case applied > 0:
		log.Printf("%s: took %d variable(s) the environment did not carry", path, applied)
	}
}

// envFilePath picks the file to read and says whether it was named outright.
func envFilePath() (path string, explicit bool) {
	if named := env(envEnvFile); named != "" {
		return named, true
	}
	if data := env(envPluginData); data != "" {
		return filepath.Join(data, envFileName), false
	}
	return "", false
}

// applyEnvFile reads path and sets every variable the environment does not
// already carry, returning how many it set. A name that already has a value is
// left alone without a word: that is the normal case for an install that
// configures the server properly and keeps a file around from an older one.
func applyEnvFile(path string) (int, error) {
	f, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer f.Close()

	values, err := parseEnvFile(f)
	if err != nil {
		return 0, err
	}
	applied := 0
	for name, value := range values {
		// "Set but empty" counts as absent, the way firstConfigured already
		// reads it: a manifest that spells out a variable it has no value for
		// hands over a blank, and a blank is what the file is there to fill.
		if env(name) != "" {
			continue
		}
		if err := os.Setenv(name, value); err != nil {
			return applied, fmt.Errorf("setting %s: %w", name, err)
		}
		applied++
	}
	return applied, nil
}

// parseEnvFile reads the subset of .env every writer of one agrees on: blank
// lines and # comments are skipped, NAME=VALUE is a variable, a leading
// "export " is tolerated because people paste shell lines, and a value wrapped
// in matching quotes is unwrapped. There is no expansion of any kind — a
// $OTHER or a ${OTHER} in a value is part of the value, since the one thing
// this file holds is secrets, and a secret with a $ in it is not a reference.
func parseEnvFile(r io.Reader) (map[string]string, error) {
	values := make(map[string]string)
	scanner := bufio.NewScanner(r)
	for line := 1; scanner.Scan(); line++ {
		text := strings.TrimSpace(scanner.Text())
		if text == "" || strings.HasPrefix(text, "#") {
			continue
		}
		text = strings.TrimPrefix(text, "export ")
		name, value, ok := strings.Cut(text, "=")
		if !ok {
			return nil, fmt.Errorf("line %d is not NAME=VALUE", line)
		}
		name = strings.TrimSpace(name)
		if name == "" {
			return nil, fmt.Errorf("line %d has no name", line)
		}
		values[name] = unquote(strings.TrimSpace(value))
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	return values, nil
}

// unquote strips one pair of matching quotes. An unquoted value keeps every
// character it has, including the spaces inside it.
func unquote(value string) string {
	if len(value) < 2 {
		return value
	}
	for _, quote := range []byte{'"', '\''} {
		if value[0] == quote && value[len(value)-1] == quote {
			return value[1 : len(value)-1]
		}
	}
	return value
}
