package firecrawl

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"slices"
	"strings"
)

// Page is one web page rendered to Markdown, with the images on it when the
// read asked for them.
type Page struct {
	Markdown string  `json:"markdown"`
	Images   []Image `json:"images"`
	// HTML is the page's cleaned markup, asked for only alongside the images:
	// the image list is addresses and nothing else, and the alt text that ties
	// a picture to a subject lives on the <img> tags. It is folded into Images
	// and is not meant to be read by anything else.
	HTML     string       `json:"html"`
	Metadata PageMetadata `json:"metadata"`
}

// Image is one picture on the page: where it is, and what the page says it
// is. The alt text is the whole value of asking the provider for this list
// rather than reading the Markdown — it is what ties a photograph to the
// person or the product it shows, and Markdown carries it only for the images
// that survived the main-content filter.
type Image struct {
	URL string `json:"url"`
	Alt string `json:"alt"`
}

// UnmarshalJSON reads both shapes the format is served in — a bare address,
// or an object carrying the address under one of the names an extractor might
// use. A list of strings is still useful (the addresses are the point), so it
// is decoded rather than refused.
func (i *Image) UnmarshalJSON(data []byte) error {
	var url string
	if err := json.Unmarshal(data, &url); err == nil {
		i.URL = strings.TrimSpace(url)
		return nil
	}
	var obj map[string]any
	if err := json.Unmarshal(data, &obj); err != nil {
		return fmt.Errorf("image is neither an address nor an object: %w", err)
	}
	i.URL = firstString(obj, "url", "src", "imageUrl", "href")
	i.Alt = firstString(obj, "alt", "altText", "caption", "title")
	return nil
}

// firstString picks the first key that carries a non-empty string.
func firstString(obj map[string]any, keys ...string) string {
	for _, key := range keys {
		if s, ok := obj[key].(string); ok && strings.TrimSpace(s) != "" {
			return strings.TrimSpace(s)
		}
	}
	return ""
}

// PageMetadata is the part of the provider's metadata worth reporting back:
// the address as it saw it — which is not always the one we asked for, a
// redirect being the usual reason — its title, and the status the site
// answered with. The provider sends more; nothing here reads it.
type PageMetadata struct {
	SourceURL string `json:"sourceURL"`
	URL       string `json:"url"`
	Title     string `json:"title"`
	// OGImage is the image the page nominates as its own — the share card.
	// On a company's front page that is almost always the logo or the brand
	// shot, and it is stated as data rather than inferred from a banner,
	// which makes it the one picture on a page a reader can trust to be about
	// the site's owner. It survives the main-content filter, which the header
	// logo does not.
	OGImage string `json:"ogImage"`
	// Favicon is the site's own icon. It is here for the one picture a
	// company site often does not otherwise offer as an image at all: the
	// logo, which is frequently inline SVG in the markup — invisible to any
	// scan of <img> tags — while the favicon is a real file, usually a PNG,
	// and unambiguously about the site's owner.
	Favicon    string `json:"favicon"`
	StatusCode int    `json:"statusCode"`
}

// Address is the page's own address as the provider ended up at it, falling
// back to the one we asked for. A redirect is worth reporting: a company's
// "/contacts" that lands on "/en/contact-us" is a different page than the one
// the caller thought it was reading.
func (p Page) Address() string {
	for _, candidate := range []string{p.Metadata.URL, p.Metadata.SourceURL} {
		if s := strings.TrimSpace(candidate); s != "" {
			return s
		}
	}
	return ""
}

// scrapeRequest is the body of POST /v2/scrape: the url plus how the page is
// rendered. Field order is the canonical body order, and Go marshals struct
// fields in declaration order.
type scrapeRequest struct {
	URL string `json:"url"`
	scrapeOptions
}

// scrapeOptions is how a page is rendered: Markdown of the main content only,
// without ads or inline base64 images — the reader is a model with a context
// window, and a navigation bar repeated on every page is noise in it.
type scrapeOptions struct {
	Formats            []string `json:"formats"`
	OnlyMainContent    bool     `json:"onlyMainContent"`
	RemoveBase64Images bool     `json:"removeBase64Images"`
	BlockAds           bool     `json:"blockAds"`
}

var defaultScrapeOptions = scrapeOptions{
	Formats:            []string{"markdown"},
	OnlyMainContent:    true,
	RemoveBase64Images: true,
	BlockAds:           true,
}

// imageScrapeOptions asks for the page's pictures alongside its text.
//
// The text is still main-content only: a reader of a page wants what it says,
// not its navigation. The image list is not filtered that way by us — asking
// for it is the point, and an inventory missing the one picture the page is
// about would be worse than no inventory.
var imageScrapeOptions = func() scrapeOptions {
	opts := defaultScrapeOptions
	// The HTML rides along for the alt texts, which the image list does not
	// carry: the provider answers it with bare addresses, and an address
	// identifies nobody. The markup is read here and never handed on, so it
	// costs a larger response and not a larger context.
	opts.Formats = []string{"markdown", "images", "html"}
	return opts
}()

// scrapeResponse is the body of POST /v2/scrape.
type scrapeResponse struct {
	Data Page `json:"data"`
}

// Scrape renders one page to Markdown in a single synchronous call. It does
// no traversal: just this URL, its main content, as Markdown — a site root
// and a deep link are read the same way, and whether to read further into the
// site is the caller's decision, not the connector's.
func (c *Client) Scrape(ctx context.Context, pageURL string) (Page, error) {
	return c.scrape(ctx, pageURL, defaultScrapeOptions)
}

// ScrapeWithImages reads the page and, with it, the list of pictures on it.
//
// It is a separate call rather than an option on every read because the
// inventory is only worth its bytes when somebody is going to bind a picture
// to a node: a page read for what it says comes back without it.
func (c *Client) ScrapeWithImages(ctx context.Context, pageURL string) (Page, error) {
	return c.scrape(ctx, pageURL, imageScrapeOptions)
}

func (c *Client) scrape(ctx context.Context, pageURL string, opts scrapeOptions) (Page, error) {
	if err := ValidatePageURL(pageURL); err != nil {
		return Page{}, err
	}
	body, err := json.Marshal(scrapeRequest{URL: strings.TrimSpace(pageURL), scrapeOptions: opts})
	if err != nil {
		return Page{}, &Error{Message: "marshal scrape request: " + err.Error(), Err: err}
	}
	var out scrapeResponse
	if err := c.do(ctx, http.MethodPost, c.baseURL+"/v2/scrape", body, &out); err != nil {
		return Page{}, err
	}
	page := out.Data
	if slices.Contains(opts.Formats, "images") {
		page.Images = withAlts(page.Images, page.HTML)
	}
	page.HTML = ""
	return page, nil
}

// ValidatePageURL is the cheap deterministic gate before the call: a real
// host, no fragment, http or https, and not a host we can already tell is
// private. It is not a security boundary — the page is fetched by the
// provider, not by this process, and what it may reach is the provider's
// policy. It is here so an address that was never going to work comes back as
// a sentence the model can act on instead of as a 400 from a third party.
func ValidatePageURL(raw string) error {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil {
		return &Error{Message: "unparseable url: " + err.Error(), Err: err}
	}
	if !slices.Contains([]string{"https", "http"}, u.Scheme) {
		return &Error{Message: "url must be http or https, and this one is " + describeScheme(u.Scheme)}
	}
	if u.Fragment != "" {
		return &Error{Message: "url must not carry a #fragment — it names a place inside a page, and the whole page is what is read"}
	}
	host := u.Hostname()
	if host == "" {
		return &Error{Message: "url has no host"}
	}
	if isPrivateHost(host) {
		return &Error{Message: "host " + host + " is private, and the provider reads the page from the public internet"}
	}
	return nil
}

// describeScheme names an empty scheme for what it is: the usual cause is an
// address pasted without one, and "scheme \"\"" does not say that.
func describeScheme(scheme string) string {
	if scheme == "" {
		return "missing one (write the address with https://)"
	}
	return strings.ToLower(scheme)
}

// isPrivateHost catches the hosts we can rule out without resolving:
// localhost, the .local mDNS suffix, and a literal private or loopback IP.
func isPrivateHost(host string) bool {
	h := strings.ToLower(strings.TrimSuffix(host, "."))
	if h == "localhost" || strings.HasSuffix(h, ".local") {
		return true
	}
	if ip := net.ParseIP(h); ip != nil {
		return ip.IsLoopback() || ip.IsPrivate() || ip.IsUnspecified() ||
			ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast()
	}
	return false
}
