package main

import (
	"bytes"
	"context"
	"encoding/json"
	"image"
	"image/color"
	"image/png"
	"os"
	"strconv"
	"strings"
	"testing"
)

// The live tests drive the tools against a real Simulator gateway,
// through the same entry point an MCP client calls. They are opt-in — a plain
// `go test ./...` must not start writing to somebody's twin — and take their
// configuration from the same environment the server itself reads:
//
//	SIM_LIVE=1 SIM_BASE_URL=... SIM_API_KEY=... SIM_LAYER=<uuid> \
//	    go test . -run TestLiveExportGraph -v
//
//	make live-export LAYER=<uuid> OUT_DIR=./out
//	make live-apply OPS=./out/graph.ops.yaml            # dry run: prints the diff
//	make live-apply OPS=./out/graph.ops.yaml WRITE=1    # writes it
//	make live-find TYPE=suppliers OUT_DIR=./out VALUES=a,b  # does it exist yet
//	make live-read URL=https://example.com/about            # what a page reads as

// liveOrSkip skips unless the run asked for the network and has a credential.
func liveOrSkip(t *testing.T) {
	t.Helper()
	if os.Getenv("SIM_LIVE") == "" {
		t.Skip("live test: set SIM_LIVE=1 (or use the make targets)")
	}
	if env(envAPIKey) == "" {
		t.Skipf("no credential: set %s", envAPIKey)
	}
	if env(envBaseURL) == "" {
		t.Skipf("no gateway: set %s", envBaseURL)
	}
}

// callToolText runs one tool the way the protocol does and returns its text,
// failing the test when the tool reported an error.
func callToolText(t *testing.T, name string, args map[string]any) string {
	t.Helper()
	raw, err := json.Marshal(map[string]any{"name": name, "arguments": args})
	if err != nil {
		t.Fatalf("marshal params: %v", err)
	}
	res, rerr := callTool(raw)
	if rerr != nil {
		t.Fatalf("%s: protocol error %+v", name, rerr)
	}
	text, isErr := toolText(t, res)
	if isErr {
		t.Fatalf("%s failed:\n%s", name, text)
	}
	return text
}

// toolText unpacks a CallToolResult into its text and its error flag.
func toolText(t *testing.T, res any) (string, bool) {
	t.Helper()
	raw, err := json.Marshal(res)
	if err != nil {
		t.Fatalf("marshal result: %v", err)
	}
	var out struct {
		Content []struct {
			Text string `json:"text"`
		} `json:"content"`
		IsError bool `json:"isError"`
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatalf("decode result: %v", err)
	}
	var b strings.Builder
	for _, c := range out.Content {
		b.WriteString(c.Text)
	}
	return b.String(), out.IsError
}

// TestLiveExportGraph exports a real layer into a real directory: the layer,
// one form per type and one actor per node, projected into the three files.
// The output goes to a temp directory unless SIM_OUT_DIR names one.
func TestLiveExportGraph(t *testing.T) {
	liveOrSkip(t)

	// SIM_LAYER is a knob of this test, not of the server: the tool takes the
	// layer as an argument and has nothing to fall back to.
	layer := env("SIM_LAYER")
	if layer == "" {
		t.Skip("no layer: set SIM_LAYER (or run `make live-export LAYER=<uuid>`)")
	}
	args := map[string]any{"layer": layer}

	outDir := env("SIM_OUT_DIR")
	if outDir == "" {
		outDir = t.TempDir()
	}
	args["dir"] = outDir

	t.Logf("exporting into %s", outDir)
	t.Logf("\n%s", callToolText(t, "export_graph", args))

	// The skill reads all three, and an export missing one of them is worse
	// than no export: the next ops file would resolve against a stale file.
	for _, name := range []string{"graph.values.yaml", "graph.ids.json", "types.schema.yaml"} {
		info, err := os.Stat(outDir + "/" + name)
		if err != nil {
			t.Fatalf("stat %s: %v", name, err)
		}
		t.Logf("%8d bytes  %s", info.Size(), name)
	}

	values, err := os.ReadFile(outDir + "/graph.values.yaml")
	if err != nil {
		t.Fatalf("read the values file: %v", err)
	}
	t.Logf("graph.values.yaml:\n%s", head(string(values), 40))
}

// TestLiveFindRecords runs the pre-create check against a real register. It
// is the tool whose behaviour a fixture cannot vouch for: the listing route
// wants an explicit accId, which is read off the form rather than configured,
// and its `q` filter is exact and case-sensitive in a way only the gateway
// can confirm.
func TestLiveFindRecords(t *testing.T) {
	liveOrSkip(t)

	slug := env("SIM_TYPE")
	if slug == "" {
		t.Skip("no type: set SIM_TYPE (or run `make live-find TYPE=<slug>`)")
	}
	dir := env("SIM_OUT_DIR")
	if dir == "" {
		t.Skip("no export to resolve the slug against: set SIM_OUT_DIR to a directory " +
			"holding types.schema.yaml (run `make live-export` first)")
	}
	raw := env("SIM_VALUES")
	if raw == "" {
		t.Skip("nothing to check: set SIM_VALUES=<comma-separated identities>")
	}

	var values []string
	for _, v := range strings.Split(raw, ",") {
		if v = strings.TrimSpace(v); v != "" {
			values = append(values, v)
		}
	}

	args := map[string]any{"type": slug, "dir": dir, "values": values}
	if raw := env("SIM_FIELDS"); raw != "" {
		var fields []string
		for _, f := range strings.Split(raw, ",") {
			if f = strings.TrimSpace(f); f != "" {
				fields = append(fields, f)
			}
		}
		args["fields"] = fields
	}
	t.Logf("\n%s", callToolText(t, "find_records", args))
}

// TestLiveApplyOps plans a real ops file against a real layer and, only when
// SIM_WRITE is set, applies it. The default is a dry run, and deliberately
// so: the plan is what a human reads before a document import changes a twin.
func TestLiveApplyOps(t *testing.T) {
	liveOrSkip(t)

	opsPath := env("SIM_OPS")
	if opsPath == "" {
		t.Skip("no ops file: set SIM_OPS (or run `make live-apply OPS=<file>`)")
	}
	write := os.Getenv("SIM_WRITE") != ""

	args := map[string]any{"ops": opsPath, "write": write}
	if layer := env("SIM_LAYER"); layer != "" {
		args["layer"] = layer
	}
	if os.Getenv("SIM_PARTIAL") != "" {
		args["partial"] = true
	}
	if os.Getenv("SIM_KEEP_EXPORT") != "" {
		args["keep_export"] = true
	}

	t.Logf("%s %s", map[bool]string{true: "applying", false: "planning"}[write], opsPath)
	t.Logf("\n%s", callToolText(t, "apply_graph", args))

	if !write {
		t.Log("dry run only — nothing written; add WRITE=1 to apply")
	}
}

// TestLiveReadPage renders a real page through a real Firecrawl instance.
// This one is worth running by hand for what a fixture cannot show: how much
// of a page survives "main content only", which is the whole question when
// deciding whether a site is worth reading further.
//
//	SIM_LIVE=1 FIRECRAWL_API_KEY=... FIRECRAWL_URL=https://example.com/about \
//	    go test . -run TestLiveReadPage -v
//
// The picture inventory comes with it — the other thing a fixture cannot show,
// since what the provider puts in that list (bare addresses, or objects
// carrying the alt text) is what an ops file's `picture:` can be written from
// at all. FIRECRAWL_IMAGES=0 reads the page without it.
func TestLiveReadPage(t *testing.T) {
	if os.Getenv("SIM_LIVE") == "" {
		t.Skip("live test: set SIM_LIVE=1 (or use the make targets)")
	}
	if env(envFirecrawlAPIKey) == "" && env(envDefaultFirecrawlAPIKey) == "" {
		t.Skipf("no credential: set %s", envFirecrawlAPIKey)
	}
	pageURL := env("FIRECRAWL_URL")
	if pageURL == "" {
		t.Skip("no page: set FIRECRAWL_URL (or run `make live-read URL=<address>`)")
	}
	// Only the head: a page is thousands of lines of Markdown, and what a
	// reader of this log wants is whether the right page came back at all.
	args := map[string]any{"url": pageURL}
	if env("FIRECRAWL_IMAGES") == "0" {
		args["images"] = false
	}
	t.Logf("\n%s", head(callToolText(t, "read_page", args), 40))
}

// TestLiveUploadPicture puts a small generated PNG into a real workspace's
// storage — the one call in the picture path that a fixture cannot vouch for,
// since what it proves is that the gateway takes this multipart body and
// answers with a storage path a node's `picture` can carry.
//
// It writes: the file stays in that workspace's storage, attached to nothing.
//
//	SIM_LIVE=1 SIM_UPLOAD=1 SIM_BASE_URL=... SIM_API_KEY=... \
//	    SIM_WORKSPACE_ID=<uuid> go test . -run TestLiveUploadPicture -v
func TestLiveUploadPicture(t *testing.T) {
	liveOrSkip(t)
	if os.Getenv("SIM_UPLOAD") == "" {
		t.Skip("this one writes a file to a real workspace: set SIM_UPLOAD=1 to allow it")
	}
	cfg, err := loadConfig()
	if err != nil {
		t.Fatalf("config: %v", err)
	}
	if cfg.WorkspaceID == "" {
		t.Skipf("no workspace: set %s", envWorkspaceID)
	}

	// A 64x64 PNG, built here rather than read from disk: the test has to
	// work in a checkout with no fixtures, and the bytes only have to be a
	// real image.
	img := image.NewRGBA(image.Rect(0, 0, 64, 64))
	for x := range 64 {
		for y := range 64 {
			img.Set(x, y, color.RGBA{R: uint8(x * 4), G: uint8(y * 4), B: 128, A: 255})
		}
	}
	var buf bytes.Buffer
	if err := png.Encode(&buf, img); err != nil {
		t.Fatalf("encode png: %v", err)
	}

	up, err := cfg.client().UploadFile(context.Background(), cfg.WorkspaceID,
		"migration-factory-plugin-live-check.png", "image/png", buf.Bytes())
	if err != nil {
		t.Fatalf("UploadFile: %v", err)
	}
	t.Logf("stored %d bytes as %q (status %q, id %d) — this is what a node's picture carries",
		up.Size, up.FileName, up.Status, up.ID)
	if up.FileName == "" {
		t.Error("the gateway returned no storage path")
	}
}

// head returns the first n lines of s, marking the cut when there are more.
func head(s string, n int) string {
	lines := strings.Split(s, "\n")
	if len(lines) <= n {
		return s
	}
	return strings.Join(lines[:n], "\n") + "\n… (" + strconv.Itoa(len(lines)-n) + " more lines)"
}
