// Package firecrawl renders a web page to Markdown through a Firecrawl v2
// instance — one route, POST /v2/scrape: one address in, the text of that one
// page out.
//
// Crawling is deliberately absent. The tool over this client exists so the
// agent can follow a link it judged worth following, one page at a time; a
// crawl would hand it a whole site it never asked for, and the service that
// hands a website source over has already made the same choice — a website
// source arrives as the Markdown of exactly one address.
//
// Auth is one header, Authorization: Bearer <key>. The page itself is fetched
// by Firecrawl and not by this process: what a given address may reach is the
// provider's policy, and the only host this client ever dials is the instance
// it was configured with.
package firecrawl

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

// DefaultBaseURL is the shared dev Firecrawl gateway — the same instance the
// service renders its website sources through.
const DefaultBaseURL = "https://dev-firecrawl.corezoid.com"

// DefaultTimeout caps one scrape. A page renders in seconds; a slow site with
// a heavy front end takes tens of them, and a request still hanging after two
// minutes is not going to answer.
const DefaultTimeout = 2 * time.Minute

// maxResponseBytes caps the body read into memory. One page of Markdown is
// kilobytes; a runaway response is refused rather than pulled in whole.
const maxResponseBytes = 8 << 20 // 8 MiB

// Client talks to one Firecrawl instance with one API key. The zero value is
// unusable — build it with New. It is safe for concurrent use.
type Client struct {
	baseURL   string
	apiKey    string
	userAgent string
	http      *http.Client
}

// Option customises a Client at construction.
type Option func(*Client)

// WithAPIKey sets the Bearer key. Without one the instance answers 401.
func WithAPIKey(key string) Option {
	return func(c *Client) { c.apiKey = strings.TrimSpace(key) }
}

// WithUserAgent overrides the User-Agent header.
func WithUserAgent(ua string) Option {
	return func(c *Client) { c.userAgent = ua }
}

// New builds a client for the Firecrawl at baseURL (empty means
// DefaultBaseURL). A base with no scheme gets https, because a bare host is
// what an operator types; nothing else about it is guessed.
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

// NormalizeBaseURL trims blanks and a trailing slash and adds https to a bare
// host. An empty input stays empty, so New can tell it from a configured one.
func NormalizeBaseURL(input string) string {
	s := strings.TrimRight(strings.TrimSpace(input), "/")
	if s == "" {
		return ""
	}
	if !strings.Contains(s, "://") {
		s = "https://" + s
	}
	return s
}

// do executes one JSON request against the instance and decodes the body into
// out. A non-2xx becomes an *Error carrying the provider's own message, so the
// model reading the tool result learns whether the page was blocked, the key
// refused or the instance itself unwell.
func (c *Client) do(ctx context.Context, method, u string, body []byte, out any) error {
	var reader io.Reader
	if body != nil {
		reader = bytes.NewReader(body)
	}
	req, err := http.NewRequestWithContext(ctx, method, u, reader)
	if err != nil {
		return fmt.Errorf("firecrawl: build request: %w", err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json; charset=utf-8")
	}
	if c.apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+c.apiKey)
	}
	req.Header.Set("User-Agent", c.userAgent)

	resp, err := c.http.Do(req)
	if err != nil {
		if ctx.Err() != nil {
			return fmt.Errorf("firecrawl: %s %s: %w", method, redactPath(u), ctx.Err())
		}
		return &Error{Message: fmt.Sprintf("%s %s: %v", method, redactPath(u), err), Err: err}
	}
	defer resp.Body.Close()

	raw, err := io.ReadAll(io.LimitReader(resp.Body, maxResponseBytes+1))
	if err != nil {
		return &Error{Message: "read response: " + err.Error(), Err: err}
	}
	if len(raw) > maxResponseBytes {
		return &Error{Message: fmt.Sprintf("response exceeds %d bytes", maxResponseBytes)}
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return parseError(resp.StatusCode, raw)
	}
	if out != nil {
		if err := json.Unmarshal(raw, out); err != nil {
			return fmt.Errorf("firecrawl: decode %s response: %w", redactPath(u), err)
		}
	}
	return nil
}

// redactPath keeps a URL's path for a message without repeating the whole of
// it back at the caller.
func redactPath(raw string) string {
	if u, err := url.Parse(raw); err == nil {
		return u.Path
	}
	return raw
}
