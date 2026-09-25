package main

import (
	"fmt"
	"log"
	"os"
	"strconv"
	"strings"
	"sync"

	"migration-factory-plugin-mcp/internal/firecrawl"
	"migration-factory-plugin-mcp/internal/simulator"
)

// The whole configuration of this server, and the whole of it comes from the
// environment: an MCP client starts it with a working directory of its own
// choosing, so there is nothing to find relative to the binary. A host that
// filters the environment instead of passing it through leaves the credentials
// in a .env file the host itself points at, which envfile.go folds into that
// same environment before any of this is read — a way in, not a second place
// to look.
//
// Two groups of variables and nothing else is configurable: the gateway, its
// key and the key's fallback for the layer, and the same split of base url,
// key and fallback for the page reader — plus the working directory launch-mcp
// hands over, which is not configuration at all. Everything a run decides —
// which layer, which directory, which address, dry run or write — is an
// argument of the tool call, where the caller can see it and change it per
// call.
//
// The groups are loaded apart, by loadConfig and loadFirecrawlConfig: a server
// with no Firecrawl key still exports and applies layers, and a server with no
// Simulator key still reads a page. Refusing every tool because the half the
// caller is not using was left unset is how a fresh install looks broken when
// it is not.
const (
	// envBaseURL is the Simulator gateway. A bare host works —
	// "mw.simulator.company" and "https://mw.simulator.company/papi/1.0" are
	// the same target. A caller that knows where the layer lives — mf-api,
	// which sets it on the cc-api project from the session's workspace — wins;
	// unset is not an error but the client's own DefaultBaseURL, the same
	// split the page reader has below. The default lives there rather than in
	// a manifest because a portable Agent Plugins v1 mcp.json has no
	// "${VAR:-fallback}" to write it with: its values are literal, and a
	// literal would displace the caller that does know better.
	envBaseURL = "SIM_BASE_URL"
	// envAPIKey is the workspace API key issued at account.corezoid.com. It
	// is scoped to one workspace on one gateway, so it and SIM_BASE_URL have
	// to name the same environment. The key carries the workspace for every
	// route here but one: the actor listing also wants it as an explicit
	// accId, which find_records reads off the form it is probing rather than
	// asking for it in configuration.
	envAPIKey = "SIM_API_KEY"
	// envWorkspaceID is the workspace (accId) uploads go into. The API key
	// carries the workspace for every route the layer needs, so this is set
	// only for what the key alone cannot say: a file upload names its
	// workspace in the path, and a node's picture is a file. mf-api already
	// sends it, from the session's own workspace. Unset is not fatal — the
	// form behind the actor is asked instead, which costs one read.
	envWorkspaceID = "SIM_WORKSPACE_ID"
	// envGroupID is the Single Account group the session's people are in, and
	// every record a run creates is shared to it. A record is an actor of its
	// form and on no layer, so the share the layer carries never reaches it;
	// a hole filled on the layer needs nothing. mf-api sets it from the
	// session's own group. Unset — a run mf-api did not start — shares
	// nothing, and says so once.
	envGroupID = "SIM_GROUP_ID"
	// envDefaultAPIKey is what envAPIKey falls back to, and it is the one a
	// deployer pins. The two are separate variables rather than one with a
	// default because the caller that has a real key — mf-api, which starts
	// the project with the session's own key in SIM_API_KEY — sets it in the
	// process environment, where it cannot displace a value .mcp.json spells
	// out. Pinning the default under its own name leaves SIM_API_KEY free for
	// that. The .mcp.json in this repository pins nothing: it is public, so
	// an unconfigured install has no key and says so when a tool is called.
	envDefaultAPIKey = "DEFAULT_SIM_API_KEY"

	// envFirecrawlBaseURL is the Firecrawl v2 instance read_page renders a
	// page through. Unset is fine and usual — the client falls back to the
	// shared dev instance, which is the one the service itself renders
	// website sources with, so a page read here and a page handed over as a
	// source come back rendered the same way.
	envFirecrawlBaseURL = "FIRECRAWL_BASE_URL"
	// envFirecrawlAPIKey is that instance's key. It is the same split as the
	// Simulator pair below: a caller with a key of its own sets this one in
	// the process environment, where it cannot be displaced by a value
	// .mcp.json spells out.
	envFirecrawlAPIKey = "FIRECRAWL_API_KEY"
	// envDefaultFirecrawlAPIKey is what envFirecrawlAPIKey falls back to: the
	// same split as the Simulator pair, and likewise left unset by .mcp.json.
	envDefaultFirecrawlAPIKey = "DEFAULT_FIRECRAWL_API_KEY"

	// envCWD is not configuration in the ordinary sense: it is how launch-mcp
	// hands over the directory the MCP client started it in, which `go run -C`
	// would otherwise replace with the module's own. See resolve. It is worth
	// setting by hand in one case — a host that starts this server inside the
	// plugin's own directory rather than in the user's project, where relative
	// paths would otherwise resolve against the package. launch-mcp keeps a
	// value that is already set.
	envCWD = "MIGRATION_FACTORY_PLUGIN_CWD"
)

// config is the resolved environment.
type config struct {
	BaseURL     string
	APIKey      string
	WorkspaceID string
	GroupID     int
}

// loadConfig reads the environment. Only the key is required, and the message
// names it, because that is the most likely thing to be wrong with a fresh
// install; an absent gateway is the client's own default.
func loadConfig() (*config, error) {
	cfg := &config{
		// Through firstConfigured too: .mcp.json spells the gateway
		// "${SIM_BASE_URL:-…}", and a client that does not expand that syntax
		// would hand the placeholder over as a host — better dropped here for
		// the client's default than dialled.
		BaseURL:     firstConfigured(env(envBaseURL)),
		APIKey:      firstConfigured(env(envAPIKey), env(envDefaultAPIKey)),
		WorkspaceID: firstConfigured(env(envWorkspaceID)),
		GroupID:     groupID(),
	}
	if cfg.APIKey == "" {
		return nil, fmt.Errorf("no Simulator API key: set %s in the MCP server's env (or %s for a fallback)",
			envAPIKey, envDefaultAPIKey)
	}
	return cfg, nil
}

// noGroupNotice makes the "shares nothing" line a once-per-process one: the
// environment does not change between tool calls, and a line per apply would
// bury the one that matters.
var noGroupNotice sync.Once

// groupID reads the group records are shared to. Anything but a positive
// integer means no group, and that is said in the log rather than swallowed:
// a run that shares nothing looks exactly like a run that was never asked to,
// and this is the only place that knows which it is.
func groupID() int {
	raw := firstConfigured(env(envGroupID))
	if id, err := strconv.Atoi(raw); err == nil && id > 0 {
		return id
	}
	noGroupNotice.Do(func() {
		what := "unset"
		if raw != "" {
			what = fmt.Sprintf("%q is not a positive integer", raw)
		}
		log.Printf("%s %s: records a run creates are shared to no group", envGroupID, what)
	})
	return 0
}

// client builds the Simulator client this config describes.
func (c *config) client() *simulator.Client {
	return simulator.New(c.BaseURL,
		simulator.WithAPIKey(c.APIKey),
		simulator.WithUserAgent(serverName+"/"+serverVersion))
}

// firecrawlConfig is the page reader's half of the environment.
type firecrawlConfig struct {
	BaseURL string
	APIKey  string
}

// loadFirecrawlConfig reads it. Only the key is required: an absent instance
// means the default one, while an absent key means every page comes back as a
// 401 from a third party, which is worth saying by name here instead.
func loadFirecrawlConfig() (*firecrawlConfig, error) {
	cfg := &firecrawlConfig{
		BaseURL: firstConfigured(env(envFirecrawlBaseURL)),
		APIKey:  firstConfigured(env(envFirecrawlAPIKey), env(envDefaultFirecrawlAPIKey)),
	}
	if cfg.APIKey == "" {
		return nil, fmt.Errorf("no Firecrawl API key: set %s in the MCP server's env (or %s for a fallback) — "+
			"reading a page needs one, exporting and applying a layer does not",
			envFirecrawlAPIKey, envDefaultFirecrawlAPIKey)
	}
	return cfg, nil
}

// client builds the Firecrawl client this config describes.
func (c *firecrawlConfig) client() *firecrawl.Client {
	return firecrawl.New(c.BaseURL,
		firecrawl.WithAPIKey(c.APIKey),
		firecrawl.WithUserAgent(serverName+"/"+serverVersion))
}

func env(name string) string { return strings.TrimSpace(os.Getenv(name)) }

// firstConfigured picks the first variable that actually carries a value. An
// unset ${VAR} in an .mcp.json without a default is passed through literally,
// so "set" here means non-empty *and* not a bare placeholder — a "${...}"
// would otherwise be taken for a key and fail as a 401 rather than as the
// missing value it is.
func firstConfigured(values ...string) string {
	for _, v := range values {
		if v != "" && !strings.HasPrefix(v, "${") {
			return v
		}
	}
	return ""
}
