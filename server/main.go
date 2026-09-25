// Command migration-factory-plugin-mcp serves the graph tools — export a Digital Twin layer,
// apply an ops file against it, check whether a record of a type already
// exists — plus two that are not about the graph at all: read a web page as
// Markdown, so a source that is an address can be read the same way a source
// that is a file is, and record a parsed bank statement on an actor as
// transactions. All over MCP stdio, so the skills can run them from any
// directory.
//
// The protocol here is hand-rolled on purpose: MCP over stdio is newline-
// delimited JSON-RPC 2.0 with four methods worth answering, and a server this
// small is not worth a dependency.
//
// Everything it needs comes from the environment — SIM_BASE_URL and
// SIM_API_KEY for the layer, FIRECRAWL_BASE_URL and FIRECRAWL_API_KEY for the
// page reader, see config.go. Anything on stdout is protocol; logs go to
// stderr.
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"os"
)

const (
	serverName     = "migration-factory-plugin"
	serverVersion  = "0.4.0"
	latestProtocol = "2025-06-18"
)

func main() {
	log.SetFlags(0)
	log.SetPrefix(serverName + ": ")
	log.SetOutput(os.Stderr)

	// serve only ever returns the decoder's error; a closed stdin is the
	// normal end, everything else is a real stop.
	if err := serve(os.Stdin, os.Stdout); !errors.Is(err, io.EOF) {
		log.Printf("stopped: %v", err)
		os.Exit(1)
	}
}

// ---------------------------------------------------------------- transport

type request struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params,omitempty"`
}

type response struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id"`
	Result  any             `json:"result,omitempty"`
	Error   *rpcError       `json:"error,omitempty"`
}

type rpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

// serve reads requests until stdin closes. Requests are handled one at a
// time: an export takes seconds and there is nothing useful to interleave.
func serve(in io.Reader, out io.Writer) error {
	dec := json.NewDecoder(in)
	enc := json.NewEncoder(out)

	for {
		var req request
		if err := dec.Decode(&req); err != nil {
			return err
		}
		// A notification has no id and takes no response, not even an error.
		if len(req.ID) == 0 {
			continue
		}
		result, rerr := dispatch(req)
		if rerr == nil && result == nil {
			// JSON-RPC requires one of the two, and `omitempty` would drop
			// a nil result and leave a response with neither.
			result = map[string]any{}
		}
		resp := response{JSONRPC: "2.0", ID: req.ID, Result: result, Error: rerr}
		if err := enc.Encode(resp); err != nil {
			return fmt.Errorf("write response: %w", err)
		}
	}
}

func dispatch(req request) (any, *rpcError) {
	switch req.Method {
	case "initialize":
		return initialize(req.Params), nil
	case "ping":
		return map[string]any{}, nil
	case "tools/list":
		return map[string]any{"tools": toolDefs()}, nil
	case "tools/call":
		return callTool(req.Params)
	default:
		return nil, &rpcError{Code: -32601, Message: "method not found: " + req.Method}
	}
}

func initialize(params json.RawMessage) any {
	var p struct {
		ProtocolVersion string `json:"protocolVersion"`
	}
	_ = json.Unmarshal(params, &p)
	version := p.ProtocolVersion
	if version == "" {
		version = latestProtocol
	}
	return map[string]any{
		"protocolVersion": version,
		"capabilities":    map[string]any{"tools": map[string]any{}},
		"serverInfo":      map[string]any{"name": serverName, "version": serverVersion},
	}
}
