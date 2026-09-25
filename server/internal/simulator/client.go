// Package simulator is a small typed client for the Simulator.Company public
// API (pong-server `/papi/1.0`), cut down to what the graph tools need: read
// a layer's nodes and edges, read an actor and the form behind it, write an
// actor back.
//
// Auth is one header, `Authorization: Bearer <workspace API key>` — a key
// issued for one workspace on one gateway, and the only credential this
// client speaks.
package simulator

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// DefaultBaseURL is the public Simulator cloud gateway.
const DefaultBaseURL = "https://mw.simulator.company/papi/1.0"

// DefaultTimeout caps every request New's client makes.
const DefaultTimeout = 60 * time.Second

// SchemeBearer is the Authorization scheme a workspace API key goes out with.
const SchemeBearer = "Bearer"

// Client talks to one Simulator gateway on behalf of one API key. The zero
// value is unusable — build it with New. A Client is safe for concurrent use.
type Client struct {
	baseURL   string
	apiKey    string
	userAgent string
	http      *http.Client
}

// Option customises a Client at construction.
type Option func(*Client)

// WithAPIKey authenticates with a workspace API key (`Bearer <key>`). Keys are
// issued per workspace at account.corezoid.com and are scoped to one gateway.
func WithAPIKey(key string) Option {
	return func(c *Client) { c.apiKey = strings.TrimSpace(key) }
}

// WithUserAgent overrides the User-Agent header.
func WithUserAgent(ua string) Option {
	return func(c *Client) { c.userAgent = ua }
}

// New builds a client for the gateway at baseURL (empty means DefaultBaseURL).
// baseURL is normalised by NormalizeBaseURL, so "mw.simulator.company" and
// "https://mw.simulator.company/papi/1.0" are the same target.
func New(baseURL string, opts ...Option) *Client {
	c := &Client{
		baseURL:   NormalizeBaseURL(baseURL),
		userAgent: "migration-factory-plugin-mcp/1",
		http:      &http.Client{Timeout: DefaultTimeout},
	}
	if c.baseURL == "" {
		c.baseURL = DefaultBaseURL
	}
	for _, opt := range opts {
		opt(c)
	}
	return c
}

// NormalizeBaseURL turns a user-entered gateway (a bare host, a host:port or a
// full URL, with or without the /papi/<version> prefix) into a canonical base
// URL: https:// unless the host is loopback, /papi/1.0 appended when no /papi/
// segment is present, no trailing slash. An empty input stays empty.
func NormalizeBaseURL(input string) string {
	s := strings.TrimSpace(input)
	if s == "" {
		return ""
	}
	if !strings.Contains(s, "://") {
		scheme := "https"
		switch hostOf(s) {
		case "localhost", "127.0.0.1", "::1":
			scheme = "http"
		}
		s = scheme + "://" + s
	}
	s = strings.TrimRight(s, "/")
	if !strings.Contains(s, "/papi/") {
		s += "/papi/1.0"
	}
	return s
}

// hostOf extracts the bare host from a scheme-less authority, handling the
// bracketed IPv6 form ("[::1]:9000" → "::1") as well as "host", "host:port"
// and "host/path".
func hostOf(s string) string {
	if strings.HasPrefix(s, "[") {
		if end := strings.Index(s, "]"); end > 0 {
			return s[1:end]
		}
	}
	if i := strings.IndexAny(s, "/:"); i >= 0 {
		return s[:i]
	}
	return s
}

type request struct {
	method string
	path   string
	query  url.Values
	body   any
	// rawBody is a body the caller encoded itself, with the content type that
	// describes it. It is here for the one route that is not JSON — the
	// multipart upload — and it is exclusive with body.
	rawBody     []byte
	contentType string
}

// newHTTPRequest renders a request into an *http.Request.
func (c *Client) newHTTPRequest(ctx context.Context, r request) (*http.Request, error) {
	u := c.baseURL + r.path
	if len(r.query) > 0 {
		u += "?" + r.query.Encode()
	}

	var (
		body        io.Reader
		contentType string
	)
	switch {
	case r.rawBody != nil:
		body, contentType = bytes.NewReader(r.rawBody), r.contentType
	case r.body != nil:
		raw, err := json.Marshal(r.body)
		if err != nil {
			return nil, fmt.Errorf("simulator: encode request body: %w", err)
		}
		body, contentType = bytes.NewReader(raw), "application/json"
	}

	req, err := http.NewRequestWithContext(ctx, r.method, u, body)
	if err != nil {
		return nil, fmt.Errorf("simulator: build request: %w", err)
	}
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", c.userAgent)

	if c.apiKey == "" {
		return nil, ErrNoCredential
	}
	req.Header.Set("Authorization", SchemeBearer+" "+c.apiKey)

	return req, nil
}

// do performs the request and returns the raw response on 2xx. The caller owns
// the body. Non-2xx is turned into an *Error and the body is closed.
func (c *Client) do(ctx context.Context, r request) (*http.Response, error) {
	req, err := c.newHTTPRequest(ctx, r)
	if err != nil {
		return nil, err
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("simulator: %s %s: %w", r.method, r.path, err)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		defer resp.Body.Close()
		return nil, parseError(resp)
	}
	return resp, nil
}

// call performs the request and decodes the JSON body into out (nil discards
// it, which also covers an empty 204).
func (c *Client) call(ctx context.Context, r request, out any) error {
	resp, err := c.do(ctx, r)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if out == nil || resp.StatusCode == http.StatusNoContent {
		_, _ = io.Copy(io.Discard, resp.Body)
		return nil
	}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return fmt.Errorf("simulator: decode %s %s response: %w", r.method, r.path, err)
	}
	return nil
}

// get is the common shape: a GET decoded into out.
func (c *Client) get(ctx context.Context, path string, query url.Values, out any) error {
	return c.call(ctx, request{method: http.MethodGet, path: path, query: query}, out)
}

// seg escapes one path segment.
func seg(s string) string { return url.PathEscape(s) }
