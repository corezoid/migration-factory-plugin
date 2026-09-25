package graph

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"image"
	"io"
	"net"
	"net/http"
	"net/url"
	"path"
	"strings"
	"syscall"
	"time"

	// Registered for their DecodeConfig alone: the size check below reads a
	// picture's dimensions without decoding the pixels.
	_ "image/gif"
	_ "image/jpeg"
	_ "image/png"

	"migration-factory-plugin-mcp/internal/simulator"
)

// A picture is the one thing an op writes that is not text: the image a node
// is drawn with on the canvas.
//
// It is copied, never linked. Simulator stores an actor's picture as a path
// in the workspace's own storage, so an address on somebody's website has to
// be fetched and uploaded before it can be set — which is also the behaviour
// worth having: the twin keeps its own copy and it survives the redesign that
// moves the original.
//
// Everything expensive or dangerous about that happens here: the fetch (of an
// address that came out of a web page, so it is guarded like any other), the
// checks that tell a photograph from a tracking pixel, and the upload, once
// per distinct image however many ops name it.

// validatePictureURL checks a picture op before anything is fetched: an
// absolute http(s) address on the source, since that is the only thing that
// can be copied. A relative path or a data: URI is a writer's slip, and
// saying so here beats a fetch that fails for a reason nobody reads.
func validatePictureURL(from string) error {
	if strings.TrimSpace(from) == "" {
		return errors.New("`picture:` needs the address of an image")
	}
	u, err := url.Parse(strings.TrimSpace(from))
	switch {
	case err != nil:
		return fmt.Errorf("`picture:` %q is not an address: %w", from, err)
	case u.Scheme != "http" && u.Scheme != "https":
		return fmt.Errorf("`picture:` %q is not http or https", from)
	case u.Host == "":
		return fmt.Errorf("`picture:` %q names no host — a picture is copied from the source, "+
			"so the address has to be the absolute one the page carries", from)
	}
	return nil
}

// The limits a picture is taken under. They are here rather than in the skill
// because none of them is a judgement call: an image the size of a tracking
// pixel is not a photograph of anything, and a model cannot see the bytes.
const (
	// maxPictureBytes is the largest image fetched. It is generous on purpose:
	// a real site serves the portrait its CMS was given, and a bank's board
	// page came back with 7 MB JPEGs that were plainly the right pictures of
	// the right people. The bytes are already downloaded by the time the
	// limit is judged, so a tight one buys nothing and costs the faces.
	maxPictureBytes = 20 << 20
	// minPictureBytes is the floor for a format whose dimensions this build
	// cannot read (webp). Icons, spacers and tracking pixels are all below it.
	minPictureBytes = 3 << 10
	// minPictureSide rejects bullets, spacers and tracking pixels by the only
	// property that tells them apart from a picture of something. It is low
	// because a site's favicon is often the only raster image of its owner
	// anywhere on it — one came back 48x48 — and a small logo on a node beats
	// no logo. Chrome icons sit at 16-24 and still fall here.
	minPictureSide = 32
	// maxPictureRatio rejects rules, gradients and separator strips. It is
	// loose because the shape it would otherwise catch is a wordmark: a
	// company logo is wide by nature — one came back 1260x198 and was refused
	// as a "banner strip", which is the most valuable picture on a site.
	maxPictureRatio = 12
	// maxPicturesPerRun caps what one ops file can pull off a site. A page of
	// portraits is a dozen; a hundred is a gallery nobody asked for.
	maxPicturesPerRun = 50
	// pictureFetchTimeout bounds one image.
	pictureFetchTimeout = 30 * time.Second
)

// pictureTypes are the formats the graph UI renders from a storage path.
// SVG is deliberately absent: the store serves it as octet-stream and the
// canvas draws nothing, so a node would look empty after a successful write.
var pictureTypes = map[string]string{
	"image/png":  ".png",
	"image/jpeg": ".jpg",
	"image/gif":  ".gif",
	"image/webp": ".webp",
}

// pictureStore fetches, checks and uploads the images one ops file names.
//
// It is per run, and it holds three maps for three different jobs: the same
// address named twice is fetched once, the same bytes arriving under two
// addresses are uploaded once, and an image already bound to a node is not
// bound to a second one — the last is the rule that stops a team photograph
// becoming the face of all five people on the page.
type pictureStore struct {
	// workspace is the accId uploads go to, from the server's configuration.
	// Empty means ask the form the actor belongs to.
	workspace string
	client    *http.Client

	byURL      map[string]string // address -> storage path
	bySHA      map[string]string // sha256 of the bytes -> storage path
	boundTo    map[string]string // storage path -> the node that carries it
	byForm     map[int]string    // form id -> its workspace
	uploads    int
	fetchedURL map[string]error // address -> why it could not be taken
}

func newPictureStore(workspace string) *pictureStore {
	return &pictureStore{
		workspace:  strings.TrimSpace(workspace),
		client:     guardedHTTPClient(pictureFetchTimeout),
		byURL:      map[string]string{},
		bySHA:      map[string]string{},
		boundTo:    map[string]string{},
		byForm:     map[int]string{},
		fetchedURL: map[string]error{},
	}
}

// path resolves one action's picture to the storage path the actor carries,
// doing the fetch and the upload the first time it sees the image.
func (s *pictureStore) path(ctx context.Context, sim *simulator.Client, a *Action) (string, error) {
	if a.Picture == "" {
		return "", nil
	}
	if err := validatePictureURL(a.Picture); err != nil {
		return "", err
	}

	stored, err := s.stored(ctx, sim, a.FormID, a.Picture)
	if err != nil {
		return "", err
	}
	if owner, taken := s.boundTo[stored]; taken && owner != a.Path {
		return "", fmt.Errorf("this image is already the picture of %s in this run — one image "+
			"belongs to one subject, and a photograph that fits two nodes has identified neither",
			owner)
	}
	s.boundTo[stored] = a.Path
	return stored, nil
}

// stored returns the storage path for an address, fetching and uploading it
// once. A failure is remembered as well: a site that refused the first read
// refuses the tenth, and the run should not spend the time finding out.
func (s *pictureStore) stored(ctx context.Context, sim *simulator.Client, formID int, from string) (string, error) {
	if p, ok := s.byURL[from]; ok {
		return p, nil
	}
	if err, ok := s.fetchedURL[from]; ok {
		return "", err
	}

	stored, err := s.fetchAndUpload(ctx, sim, formID, from)
	if err != nil {
		s.fetchedURL[from] = err
		return "", err
	}
	s.byURL[from] = stored
	return stored, nil
}

func (s *pictureStore) fetchAndUpload(ctx context.Context, sim *simulator.Client, formID int, from string) (string, error) {
	data, contentType, err := s.fetch(ctx, from)
	if err != nil {
		return "", err
	}
	name, err := pictureName(from, data, contentType)
	if err != nil {
		return "", err
	}
	if err := checkPictureSize(data, name); err != nil {
		return "", err
	}

	// Uploaded once per distinct image, not per address: a logo served from
	// two paths is one file in storage and one binding decision afterwards.
	sum := sha256.Sum256(data)
	key := hex.EncodeToString(sum[:])
	if p, ok := s.bySHA[key]; ok {
		return p, nil
	}
	if s.uploads >= maxPicturesPerRun {
		return "", fmt.Errorf("%d pictures is this run's limit and it is reached — the rest are "+
			"reported and not taken", maxPicturesPerRun)
	}

	workspace, err := s.workspaceOf(ctx, sim, formID)
	if err != nil {
		return "", err
	}
	up, err := sim.UploadFile(ctx, workspace, name, contentType, data)
	if err != nil {
		return "", fmt.Errorf("upload %s: %w", from, err)
	}
	s.uploads++
	s.bySHA[key] = up.FileName
	return up.FileName, nil
}

// fetch downloads an image through the guarded client.
func (s *pictureStore) fetch(ctx context.Context, from string) ([]byte, string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, from, nil)
	if err != nil {
		return nil, "", fmt.Errorf("fetch %s: %w", from, err)
	}
	req.Header.Set("User-Agent", "migration-factory-plugin-mcp (picture for a digital twin)")
	req.Header.Set("Accept", "image/*")

	resp, err := s.client.Do(req)
	if err != nil {
		return nil, "", fmt.Errorf("fetch %s: %w", from, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, "", fmt.Errorf("fetch %s: HTTP %d", from, resp.StatusCode)
	}

	// One byte past the cap, so a file exactly at the limit still reads whole
	// and one past it is caught rather than silently truncated into a
	// corrupt image.
	data, err := io.ReadAll(io.LimitReader(resp.Body, maxPictureBytes+1))
	if err != nil {
		return nil, "", fmt.Errorf("fetch %s: %w", from, err)
	}
	if len(data) > maxPictureBytes {
		return nil, "", fmt.Errorf("%s is larger than %d KiB — too big for a node's picture",
			from, maxPictureBytes>>10)
	}
	if len(data) == 0 {
		return nil, "", fmt.Errorf("%s returned no bytes", from)
	}
	return data, strings.TrimSpace(strings.SplitN(resp.Header.Get("Content-Type"), ";", 2)[0]), nil
}

// pictureName settles the file name and the type the upload is recorded with.
// The type decides both — the store tags a file by what multipart said, and
// the canvas renders it by that tag and the extension.
func pictureName(from string, data []byte, contentType string) (string, error) {
	ext, ok := pictureTypes[contentType]
	if !ok {
		// The header is wrong or missing often enough that the bytes get the
		// last word: the sniffer reads the magic of exactly these formats.
		sniffed := strings.SplitN(http.DetectContentType(data), ";", 2)[0]
		if ext, ok = pictureTypes[sniffed]; !ok {
			return "", fmt.Errorf("%s is %s, and a node's picture has to be a PNG, JPEG, GIF or "+
				"WebP — the canvas does not draw anything else from storage",
				from, firstNonEmpty(contentType, sniffed, "of an unknown type"))
		}
		contentType = sniffed
	}

	base := path.Base(strings.SplitN(from, "?", 2)[0])
	if i := strings.IndexAny(base, "#"); i >= 0 {
		base = base[:i]
	}
	base = strings.TrimSuffix(base, path.Ext(base))
	if base == "" || base == "." || base == "/" {
		base = "picture"
	}
	return base + ext, nil
}

// checkPictureSize rejects what is plainly not a picture of anything. It is
// the one check a reader of a web page cannot make: alt text and a caption
// say nothing about whether the image is a portrait or a one-pixel beacon.
func checkPictureSize(data []byte, name string) error {
	cfg, _, err := image.DecodeConfig(bytes.NewReader(data))
	if err != nil {
		// WebP, or a file this build cannot read the header of. The byte
		// floor is the coarse stand-in: every icon and beacon is under it.
		if len(data) < minPictureBytes {
			return fmt.Errorf("%s is %d bytes — too small to be a picture of anything", name, len(data))
		}
		return nil
	}
	if cfg.Width < minPictureSide || cfg.Height < minPictureSide {
		return fmt.Errorf("%s is %dx%d — a bullet, a spacer or a tracking pixel, not a picture of "+
			"the subject", name, cfg.Width, cfg.Height)
	}
	if ratio := float64(max(cfg.Width, cfg.Height)) / float64(min(cfg.Width, cfg.Height)); ratio > maxPictureRatio {
		return fmt.Errorf("%s is %dx%d — a rule or a separator strip by its proportions, not a "+
			"picture of the subject", name, cfg.Width, cfg.Height)
	}
	return nil
}

// workspaceOf settles which workspace an upload goes to.
//
// The configured one wins: mf-api starts the agent with the session's own
// workspace, and that is where the layer lives. Without it the form the actor
// belongs to is asked — the same fallback find_records uses, and the reason
// an unconfigured install can still set a picture.
func (s *pictureStore) workspaceOf(ctx context.Context, sim *simulator.Client, formID int) (string, error) {
	if s.workspace != "" {
		return s.workspace, nil
	}
	if ws, ok := s.byForm[formID]; ok {
		return ws, nil
	}
	form, err := sim.GetForm(ctx, formID, "id,accId,title")
	if err != nil {
		return "", fmt.Errorf("a picture is uploaded into a workspace and this server was not told "+
			"which (set SIM_WORKSPACE_ID); form %d could not be asked either: %w", formID, err)
	}
	if form.AccID == "" {
		return "", fmt.Errorf("a picture is uploaded into a workspace and this server was not told "+
			"which: set SIM_WORKSPACE_ID (form %d served no accId)", formID)
	}
	s.byForm[formID] = form.AccID
	return form.AccID, nil
}

// guardedHTTPClient is a client that cannot be steered at an internal host:
// its transport refuses any address that does not resolve to a routable
// public IP, and it will not follow a redirect that downgrades https to http.
// A picture's address comes out of a web page, which makes it exactly the
// kind of input that has to be fenced.
func guardedHTTPClient(timeout time.Duration) *http.Client {
	dialer := &net.Dialer{Timeout: 10 * time.Second, Control: guardDial}
	return &http.Client{
		Timeout: timeout,
		Transport: &http.Transport{
			DialContext:           dialer.DialContext,
			TLSHandshakeTimeout:   10 * time.Second,
			ResponseHeaderTimeout: 20 * time.Second,
		},
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) >= 5 {
				return errors.New("too many redirects")
			}
			if req.URL.Scheme != "https" && via[0].URL.Scheme == "https" {
				return fmt.Errorf("refusing redirect from https to %s", req.URL.Scheme)
			}
			return nil
		},
	}
}

// guardDial runs once the address is resolved to ip:port, just before the
// socket is opened — including for each redirect hop — so it is the one place
// that reliably sees the real target.
func guardDial(_, address string, _ syscall.RawConn) error {
	host, _, err := net.SplitHostPort(address)
	if err != nil {
		return fmt.Errorf("bad dial address %q: %w", address, err)
	}
	ip := net.ParseIP(host)
	if ip == nil {
		return fmt.Errorf("dial address %q is not an IP", host)
	}
	if !isGlobalUnicast(ip) {
		return fmt.Errorf("refusing to dial non-global address %s", ip)
	}
	return nil
}

// isGlobalUnicast reports whether an IP is a routable public address — not
// loopback, private, link-local, unspecified or multicast, and not in the
// reserved ranges the standard library does not class as private.
func isGlobalUnicast(ip net.IP) bool {
	if ip.IsLoopback() || ip.IsPrivate() || ip.IsUnspecified() ||
		ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() || ip.IsMulticast() {
		return false
	}
	for _, block := range reservedBlocks {
		if block.Contains(ip) {
			return false
		}
	}
	return ip.IsGlobalUnicast()
}

// reservedBlocks are the non-routable ranges net.IP's own predicates miss:
// carrier NAT (where one cloud keeps its metadata endpoint), "this network",
// IETF protocol assignments, benchmarking, class E, NAT64, and the two IPv6
// transition prefixes that embed an IPv4 address.
var reservedBlocks = func() []*net.IPNet {
	var out []*net.IPNet
	for _, cidr := range []string{
		"100.64.0.0/10", "0.0.0.0/8", "192.0.0.0/24", "198.18.0.0/15",
		"240.0.0.0/4", "64:ff9b::/96", "2002::/16", "2001::/32",
	} {
		_, block, err := net.ParseCIDR(cidr)
		if err != nil {
			panic(err) // a literal above is wrong — a programming error
		}
		out = append(out, block)
	}
	return out
}()
