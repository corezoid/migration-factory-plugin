package firecrawl

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// newTestClient points a client at a stub instance.
func newTestClient(t *testing.T, handler http.HandlerFunc) *Client {
	t.Helper()
	srv := httptest.NewServer(handler)
	t.Cleanup(srv.Close)
	return New(srv.URL, WithAPIKey("fc-test"))
}

func TestScrapeAsksForMarkdownOfOnePage(t *testing.T) {
	// The body is the contract with the provider: one url, markdown only,
	// main content only. A stray extra format or a crawl-shaped request is
	// how a "read this page" quietly becomes something else.
	var got struct {
		URL                string   `json:"url"`
		Formats            []string `json:"formats"`
		OnlyMainContent    bool     `json:"onlyMainContent"`
		RemoveBase64Images bool     `json:"removeBase64Images"`
		BlockAds           bool     `json:"blockAds"`
	}
	var auth, path string

	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		auth, path = r.Header.Get("Authorization"), r.URL.Path
		if err := json.NewDecoder(r.Body).Decode(&got); err != nil {
			t.Errorf("decode request: %v", err)
		}
		_, _ = w.Write([]byte(`{"success":true,"data":{"markdown":"# About\n\nWe make things.",
			"metadata":{"sourceURL":"https://example.com/about","url":"https://example.com/about",
			"title":"About us","statusCode":200}}}`))
	})

	page, err := c.Scrape(context.Background(), "https://example.com/about")
	if err != nil {
		t.Fatalf("Scrape: %v", err)
	}
	if path != "/v2/scrape" {
		t.Errorf("path = %q, want /v2/scrape — one page, not a crawl", path)
	}
	if auth != "Bearer fc-test" {
		t.Errorf("Authorization = %q, want the bearer key", auth)
	}
	if got.URL != "https://example.com/about" {
		t.Errorf("url = %q, want the page asked for", got.URL)
	}
	if strings.Join(got.Formats, ",") != "markdown" {
		t.Errorf("formats = %v, want markdown alone", got.Formats)
	}
	if !got.OnlyMainContent || !got.RemoveBase64Images || !got.BlockAds {
		t.Errorf("render options = %+v, want main content only, no base64 images, no ads", got)
	}
	if !strings.Contains(page.Markdown, "We make things.") {
		t.Errorf("markdown = %q, want the page text", page.Markdown)
	}
	if page.Metadata.Title != "About us" {
		t.Errorf("title = %q, want the provider's", page.Metadata.Title)
	}
}

func TestAddressPrefersTheURLThatAnswered(t *testing.T) {
	// A redirect makes the two differ, and the one that answered is the page
	// the text actually belongs to.
	page := Page{Metadata: PageMetadata{SourceURL: "https://example.com/contacts", URL: "https://example.com/en/contact-us"}}
	if got := page.Address(); got != "https://example.com/en/contact-us" {
		t.Errorf("Address = %q, want the redirected address", got)
	}
	// With only the address we asked for, that is the answer.
	page = Page{Metadata: PageMetadata{SourceURL: "https://example.com/contacts"}}
	if got := page.Address(); got != "https://example.com/contacts" {
		t.Errorf("Address = %q, want the requested address", got)
	}
	if got := (Page{}).Address(); got != "" {
		t.Errorf("Address = %q, want empty when the provider said nothing", got)
	}
}

func TestScrapeErrorsSayWhatToDoNext(t *testing.T) {
	// The message is the whole interface here: the model reading it decides
	// between trying again, trying another address, and giving up.
	tests := []struct {
		name     string
		status   int
		body     string
		want     string
		provider string
	}{
		{"rate limited", http.StatusTooManyRequests, `{"error":"rate limit exceeded"}`, "again in a moment", "rate limit exceeded"},
		{"blocked", http.StatusForbidden, `{"error":"blocked by robots"}`, "do not retry", "blocked by robots"},
		{"missing", http.StatusNotFound, `{"error":"not found"}`, "no page at that address", "not found"},
		{"key", http.StatusUnauthorized, `{"error":"invalid token"}`, "configuration, not the page", "invalid token"},
		{"instance", http.StatusBadGateway, `{"error":"upstream said no"}`, "reading the page again may well work", "upstream said no"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			c := newTestClient(t, func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(tc.status)
				_, _ = w.Write([]byte(tc.body))
			})
			_, err := c.Scrape(context.Background(), "https://example.com")
			if err == nil {
				t.Fatalf("Scrape of a %d succeeded", tc.status)
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Errorf("error %q does not say %q", err, tc.want)
			}
			var e *Error
			if !errors.As(err, &e) || e.Status != tc.status {
				t.Errorf("error = %#v, want an *Error carrying status %d", err, tc.status)
			}
			// The provider's own words survive — they are often the only
			// clue about which of its limits was hit.
			if !strings.Contains(err.Error(), tc.provider) {
				t.Errorf("error %q drops the provider's message %q", err, tc.provider)
			}
		})
	}
}

func TestBadAddressesNeverReachTheProvider(t *testing.T) {
	// The gate is not a security boundary — the provider fetches the page —
	// but an address that was never going to work should come back as a
	// sentence, not as somebody else's 400.
	called := false
	c := newTestClient(t, func(http.ResponseWriter, *http.Request) { called = true })

	for _, tc := range []struct{ url, want string }{
		{"example.com/about", "https://"},
		{"ftp://example.com", "http or https"},
		{"https://example.com/docs#section", "fragment"},
		{"https://localhost:8080/admin", "private"},
		{"https://127.0.0.1/", "private"},
		{"https:///path", "no host"},
	} {
		_, err := c.Scrape(context.Background(), tc.url)
		if err == nil {
			t.Errorf("Scrape(%q) succeeded, want a refusal", tc.url)
			continue
		}
		if !strings.Contains(err.Error(), tc.want) {
			t.Errorf("Scrape(%q) = %q, want it to mention %q", tc.url, err, tc.want)
		}
	}
	if called {
		t.Error("a refused address still reached the provider")
	}
}

func TestNormalizeBaseURL(t *testing.T) {
	for input, want := range map[string]string{
		"":                                    "",
		"  dev-firecrawl.corezoid.com  ":      "https://dev-firecrawl.corezoid.com",
		"https://dev-firecrawl.corezoid.com/": "https://dev-firecrawl.corezoid.com",
		"http://localhost:3002":               "http://localhost:3002",
	} {
		if got := NormalizeBaseURL(input); got != want {
			t.Errorf("NormalizeBaseURL(%q) = %q, want %q", input, got, want)
		}
	}
	// An empty base is what tells New to use the shared instance.
	if c := New(""); c.baseURL != DefaultBaseURL {
		t.Errorf("New(\"\").baseURL = %q, want %q", c.baseURL, DefaultBaseURL)
	}
}
