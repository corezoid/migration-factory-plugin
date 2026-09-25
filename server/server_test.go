package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"slices"
	"strings"
	"testing"
)

// roundTrip runs a batch of request lines through serve and returns the
// responses, which is how an MCP client sees this server: newline-delimited
// JSON on a pipe.
func roundTrip(t *testing.T, lines ...string) []response {
	t.Helper()
	var out strings.Builder
	if err := serve(strings.NewReader(strings.Join(lines, "\n")), &out); err != io.EOF {
		t.Fatalf("serve returned %v, want EOF at the end of the input", err)
	}

	var responses []response
	dec := json.NewDecoder(strings.NewReader(out.String()))
	for dec.More() {
		var r response
		if err := dec.Decode(&r); err != nil {
			t.Fatalf("decode response: %v (raw: %s)", err, out.String())
		}
		responses = append(responses, r)
	}
	return responses
}

// resultField pulls one key out of a response's result object.
func resultField(t *testing.T, r response, key string) any {
	t.Helper()
	raw, err := json.Marshal(r.Result)
	if err != nil {
		t.Fatalf("marshal result: %v", err)
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("result is not an object: %v", err)
	}
	return m[key]
}

func TestInitializeEchoesTheClientProtocol(t *testing.T) {
	// The client picks the version; answering with our own would fail the
	// handshake on a client that speaks an older one.
	got := roundTrip(t, `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}`)
	if len(got) != 1 {
		t.Fatalf("got %d responses, want 1", len(got))
	}
	if v := resultField(t, got[0], "protocolVersion"); v != "2024-11-05" {
		t.Errorf("protocolVersion = %v, want the client's own", v)
	}
	info, _ := resultField(t, got[0], "serverInfo").(map[string]any)
	if info["name"] != serverName {
		t.Errorf("serverInfo.name = %v, want %s", info["name"], serverName)
	}
}

func TestInitializeFallsBackToTheLatestProtocol(t *testing.T) {
	got := roundTrip(t, `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}`)
	if v := resultField(t, got[0], "protocolVersion"); v != latestProtocol {
		t.Errorf("protocolVersion = %v, want %s", v, latestProtocol)
	}
}

func TestNotificationsGetNoResponse(t *testing.T) {
	// A notification carries no id, and answering one is a protocol error on
	// the client side — `initialized` is sent as one by every MCP client.
	got := roundTrip(t,
		`{"jsonrpc":"2.0","method":"notifications/initialized"}`,
		`{"jsonrpc":"2.0","id":7,"method":"ping"}`)
	if len(got) != 1 {
		t.Fatalf("got %d responses, want only the ping's", len(got))
	}
	if string(got[0].ID) != "7" {
		t.Errorf("responded to id %s, want 7", got[0].ID)
	}
}

func TestToolsListAdvertisesEveryTool(t *testing.T) {
	got := roundTrip(t, `{"jsonrpc":"2.0","id":1,"method":"tools/list"}`)
	raw, _ := json.Marshal(resultField(t, got[0], "tools"))
	var tools []struct {
		Name        string         `json:"name"`
		Description string         `json:"description"`
		InputSchema map[string]any `json:"inputSchema"`
	}
	if err := json.Unmarshal(raw, &tools); err != nil {
		t.Fatalf("decode tools: %v", err)
	}
	want := []string{"export_graph", "apply_graph", "find_records", "read_page", "post_statement"}
	if len(tools) != len(want) {
		t.Fatalf("got %d tools, want %s", len(tools), strings.Join(want, ", "))
	}
	for i, tool := range tools {
		if tool.Description == "" || tool.InputSchema["type"] != "object" {
			t.Errorf("tool %s is missing a description or an object schema", tool.Name)
		}
		if tool.Name != want[i] {
			t.Errorf("tool %d = %s, want %s", i, tool.Name, want[i])
		}
	}
}

func TestUnknownMethodIsAProtocolError(t *testing.T) {
	got := roundTrip(t, `{"jsonrpc":"2.0","id":1,"method":"resources/list"}`)
	if got[0].Error == nil || got[0].Error.Code != -32601 {
		t.Fatalf("error = %+v, want -32601 method not found", got[0].Error)
	}
}

func TestEveryResponseCarriesResultOrError(t *testing.T) {
	// JSON-RPC requires exactly one of the two, and a `ping` has nothing to
	// say — an omitted empty result would leave a response with neither.
	got := roundTrip(t, `{"jsonrpc":"2.0","id":1,"method":"ping"}`)
	if got[0].Result == nil && got[0].Error == nil {
		t.Fatal("ping answered with neither a result nor an error")
	}
}

func TestToolFailureComesBackAsAToolResult(t *testing.T) {
	// A tool that cannot run reports through the result with isError, not as
	// a protocol error: the model is meant to read the text and correct
	// itself. Here the ops file does not exist.
	clearEnv(t)
	t.Setenv(envBaseURL, "mw.simulator.company")
	t.Setenv(envAPIKey, "key-1")

	got := roundTrip(t, `{"jsonrpc":"2.0","id":1,"method":"tools/call",`+
		`"params":{"name":"apply_graph","arguments":{"ops":"/nonexistent/graph.ops.yaml"}}}`)
	if got[0].Error != nil {
		t.Fatalf("protocol error %+v, want a tool result", got[0].Error)
	}
	if isErr := resultField(t, got[0], "isError"); isErr != true {
		t.Errorf("isError = %v, want true", isErr)
	}
	raw, _ := json.Marshal(resultField(t, got[0], "content"))
	if !strings.Contains(string(raw), "graph.ops.yaml") {
		t.Errorf("content %s does not name the missing file", raw)
	}
}

func TestMissingCredentialsSurfaceInTheToolResult(t *testing.T) {
	// The server starts fine without credentials — the client launches it
	// eagerly — so the missing variable has to be reported on the call.
	clearEnv(t)
	got := roundTrip(t, `{"jsonrpc":"2.0","id":1,"method":"tools/call",`+
		`"params":{"name":"export_graph","arguments":{"layer":"x"}}}`)
	raw, _ := json.Marshal(resultField(t, got[0], "content"))
	if !strings.Contains(string(raw), envAPIKey) {
		t.Errorf("content %s does not name %s", raw, envAPIKey)
	}
}

func TestUnknownToolIsAProtocolError(t *testing.T) {
	got := roundTrip(t, `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"drop_graph"}}`)
	if got[0].Error == nil || !strings.Contains(got[0].Error.Message, "drop_graph") {
		t.Fatalf("error = %+v, want one naming the unknown tool", got[0].Error)
	}
}

func TestExportWithoutALayerAsksForOne(t *testing.T) {
	// The layer is an argument of the call, not a setting: there is no
	// environment to fall back to and the message has to say so.
	clearEnv(t)
	t.Setenv(envBaseURL, "mw.simulator.company")
	t.Setenv(envAPIKey, "key-1")

	got := roundTrip(t, `{"jsonrpc":"2.0","id":1,"method":"tools/call",`+
		`"params":{"name":"export_graph","arguments":{}}}`)
	raw, _ := json.Marshal(resultField(t, got[0], "content"))
	if !strings.Contains(string(raw), "layer") {
		t.Errorf("content %s does not ask for a layer", raw)
	}
}

func TestEveryToolRequiresItsSubject(t *testing.T) {
	// export_graph without a layer, apply_graph without an ops file and
	// find_records without a type are the calls a model gets wrong, so the
	// schema marks the subject required on each.
	got := roundTrip(t, `{"jsonrpc":"2.0","id":1,"method":"tools/list"}`)
	raw, _ := json.Marshal(resultField(t, got[0], "tools"))
	var tools []struct {
		Name        string `json:"name"`
		InputSchema struct {
			Required []string `json:"required"`
		} `json:"inputSchema"`
	}
	if err := json.Unmarshal(raw, &tools); err != nil {
		t.Fatalf("decode tools: %v", err)
	}
	want := map[string][]string{
		"export_graph": {"layer"},
		"apply_graph":  {"ops"},
		// find_records needs both halves of its question: a create is licensed
		// by a value coming back absent, and a type with no values would
		// answer nothing at all.
		"find_records":   {"type", "values"},
		"read_page":      {"url"},
		"post_statement": {"account_id", "actor_id", "path"},
	}
	for _, tool := range tools {
		if strings.Join(tool.InputSchema.Required, ",") != strings.Join(want[tool.Name], ",") {
			t.Errorf("%s requires %v, want %v", tool.Name, tool.InputSchema.Required, want[tool.Name])
		}
	}
}

func TestReadPageWithoutAnAddressAsksForOne(t *testing.T) {
	clearEnv(t)
	t.Setenv(envFirecrawlAPIKey, "fc-1")

	got := roundTrip(t, `{"jsonrpc":"2.0","id":1,"method":"tools/call",`+
		`"params":{"name":"read_page","arguments":{}}}`)
	raw, _ := json.Marshal(resultField(t, got[0], "content"))
	if !strings.Contains(string(raw), "url") {
		t.Errorf("content %s does not ask for a url", raw)
	}
}

func TestReadPageNeedsOnlyItsOwnCredential(t *testing.T) {
	// The two halves of the environment are loaded apart. A page is read
	// without a Simulator key at all, and a missing Firecrawl key is reported
	// by name rather than as somebody else's 401.
	clearEnv(t)
	got := roundTrip(t, `{"jsonrpc":"2.0","id":1,"method":"tools/call",`+
		`"params":{"name":"read_page","arguments":{"url":"https://example.com"}}}`)
	raw, _ := json.Marshal(resultField(t, got[0], "content"))
	if !strings.Contains(string(raw), envFirecrawlAPIKey) {
		t.Errorf("content %s does not name %s", raw, envFirecrawlAPIKey)
	}
	if strings.Contains(string(raw), envAPIKey) {
		t.Errorf("content %s blames the Simulator key for a page it never needed one to read", raw)
	}
}

func TestReadPageRefusesAnAddressItCannotRead(t *testing.T) {
	// The refusal has to arrive as a tool result the model can act on — and
	// without a request leaving the process, which is why a private host is
	// the case worth pinning.
	clearEnv(t)
	t.Setenv(envFirecrawlAPIKey, "fc-1")

	got := roundTrip(t, `{"jsonrpc":"2.0","id":1,"method":"tools/call",`+
		`"params":{"name":"read_page","arguments":{"url":"http://localhost:8080/admin"}}}`)
	if isErr := resultField(t, got[0], "isError"); isErr != true {
		t.Errorf("isError = %v, want true", isErr)
	}
	raw, _ := json.Marshal(resultField(t, got[0], "content"))
	if !strings.Contains(string(raw), "private") {
		t.Errorf("content %s does not say why the address was refused", raw)
	}
}

func TestRelativePathsResolveAgainstTheLauncherCWD(t *testing.T) {
	// `go run -C` leaves the process in the module directory, so the launcher
	// passes the directory the client actually started it in. A relative path
	// in a tool call has to land there, not next to the sources.
	t.Setenv(envCWD, "/somewhere/a-project")

	got, err := resolve("out/graph.ops.yaml")
	if err != nil {
		t.Fatalf("resolve: %v", err)
	}
	if want := "/somewhere/a-project/out/graph.ops.yaml"; got != want {
		t.Errorf("resolve = %q, want %q", got, want)
	}

	// An absolute path is already an answer and must survive untouched.
	if got, _ := resolve("/tmp/graph.ops.yaml"); got != "/tmp/graph.ops.yaml" {
		t.Errorf("resolve of an absolute path = %q, want it unchanged", got)
	}
}

func TestRelativePathsFallBackToTheProcessCWD(t *testing.T) {
	// Run as a plain binary there is no launcher and no variable, and the
	// process's own directory is already the right one.
	t.Setenv(envCWD, "")

	got, err := resolve(".")
	if err != nil {
		t.Fatalf("resolve: %v", err)
	}
	wd, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	if got != wd {
		t.Errorf("resolve(\".\") = %q, want the process cwd %q", got, wd)
	}
}

// The picture inventory is on unless a caller turns it off: a tool argument
// nobody passes is the usual case, and a page whose pictures were never shown
// is a page no node can take a face from.
func TestReadPageAsksForPicturesUnlessToldNot(t *testing.T) {
	var asked []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Formats []string `json:"formats"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		asked = body.Formats
		_, _ = io.WriteString(w, `{"data":{"markdown":"# Acme","images":["https://acme.test/logo.png"],`+
			`"html":"<img src=\"https://acme.test/logo.png\" alt=\"Acme\">"}}`)
	}))
	defer srv.Close()

	call := func(args string) string {
		clearEnv(t)
		t.Setenv(envFirecrawlAPIKey, "fc-1")
		t.Setenv(envFirecrawlBaseURL, srv.URL)
		got := roundTrip(t, `{"jsonrpc":"2.0","id":1,"method":"tools/call",`+
			`"params":{"name":"read_page","arguments":`+args+`}}`)
		raw, _ := json.Marshal(resultField(t, got[0], "content"))
		return string(raw)
	}

	out := call(`{"url":"https://acme.test"}`)
	if !slices.Contains(asked, "images") {
		t.Errorf("formats = %v, want the pictures asked for without being told to", asked)
	}
	if !strings.Contains(out, "https://acme.test/logo.png") || !strings.Contains(out, "Acme") {
		t.Errorf("content %s carries no inventory", out)
	}

	out = call(`{"url":"https://acme.test","images":false}`)
	if slices.Contains(asked, "images") {
		t.Errorf("formats = %v, want only the text when the caller said no", asked)
	}
	if !strings.Contains(out, "# Acme") {
		t.Errorf("content %s lost the page itself", out)
	}
}
