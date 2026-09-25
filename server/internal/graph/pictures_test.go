package graph

import (
	"bytes"
	"context"
	"image"
	"image/color"
	"image/png"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"gopkg.in/yaml.v3"

	"migration-factory-plugin-mcp/internal/simulator"
)

// pngOf renders a solid PNG of the given size — the cheapest way to have
// bytes the size checks actually read rather than guess at.
func pngOf(t *testing.T, w, h int, shade uint8) []byte {
	t.Helper()
	img := image.NewRGBA(image.Rect(0, 0, w, h))
	for x := range w {
		for y := range h {
			img.Set(x, y, color.RGBA{R: shade, G: shade, B: shade, A: 255})
		}
	}
	var buf bytes.Buffer
	if err := png.Encode(&buf, img); err != nil {
		t.Fatalf("encode png: %v", err)
	}
	return buf.Bytes()
}

// pictureFixture serves images on one host and a Simulator upload route on
// another, and records what each was asked for.
type pictureFixture struct {
	site    *httptest.Server
	sim     *simulator.Client
	store   *pictureStore
	mu      sync.Mutex
	fetched []string
	uploads []string
}

func newPictureFixture(t *testing.T, images map[string][]byte) *pictureFixture {
	t.Helper()
	f := &pictureFixture{}

	f.site = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		f.fetched = append(f.fetched, r.URL.Path)
		f.mu.Unlock()
		data, ok := images[r.URL.Path]
		if !ok {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "image/png")
		_, _ = w.Write(data)
	}))
	t.Cleanup(f.site.Close)

	gateway := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := strings.TrimPrefix(r.URL.Path, "/papi/1.0")
		if strings.HasPrefix(path, "/upload/") {
			raw, _ := io.ReadAll(r.Body)
			f.mu.Lock()
			n := len(f.uploads) + 1
			name := strings.TrimPrefix(path, "/upload/") + "/stored-" + string(rune('a'+n-1)) + ".png"
			f.uploads = append(f.uploads, name)
			f.mu.Unlock()
			if len(raw) == 0 {
				w.WriteHeader(http.StatusBadRequest)
				return
			}
			_, _ = io.WriteString(w, `{"data":{"id":7,"fileName":"`+name+`","status":"clean"}}`)
			return
		}
		if path == "/forms/701" {
			_, _ = io.WriteString(w, `{"data":{"id":701,"accId":"ws-from-form","title":"ACME_EMPLOYEE"}}`)
			return
		}
		_, _ = io.WriteString(w, `{"data":{}}`)
	}))
	t.Cleanup(gateway.Close)

	f.sim = simulator.New(gateway.URL, simulator.WithAPIKey("k3y"))
	f.store = newPictureStore("ws-1")
	// The production fence refuses to dial a loopback address, which is
	// exactly what a test server is. The rest of the store — the checks, the
	// dedup, the binding — is what these tests are about, so they dial
	// through a plain client and TestGuardedDialRefusesPrivateAddresses
	// covers the fence on its own.
	f.store.client = f.site.Client()
	return f
}

func (f *pictureFixture) action(path string, formID int, from string) *Action {
	return &Action{Path: path, FormID: formID, Picture: f.site.URL + from}
}

func TestPictureOpIsAnAddress(t *testing.T) {
	var file OpsFile
	if err := yaml.Unmarshal([]byte(`
ops:
  - at: "A"
    picture: https://acme.test/team/ivan.jpg
`), &file); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if got := file.Ops[0].Picture; got != "https://acme.test/team/ivan.jpg" {
		t.Errorf("picture = %q", got)
	}
}

func TestPictureAddressRejectsWhatCannotBeFetched(t *testing.T) {
	for name, from := range map[string]string{
		"nothing":  "   ",
		"not http": "ftp://acme.test/x.png",
		"relative": "/img/x.png",
		"data uri": "data:image/png;base64,AAAA",
	} {
		if err := validatePictureURL(from); err == nil {
			t.Errorf("%s: validatePictureURL(%q) = nil, want an error", name, from)
		}
	}
	if err := validatePictureURL("https://acme.test/x.png"); err != nil {
		t.Errorf("validatePictureURL(absolute) = %v, want nil", err)
	}
}

func TestPictureIsFetchedOnceAndUploadedOnce(t *testing.T) {
	photo := pngOf(t, 200, 200, 40)
	f := newPictureFixture(t, map[string][]byte{
		"/team/ivan.jpg":   photo,
		"/team/ivan2.webp": photo, // the same image under a second address
	})

	first, err := f.store.path(context.Background(), f.sim, f.action("A", 701, "/team/ivan.jpg"))
	if err != nil {
		t.Fatalf("first: %v", err)
	}
	// The same address again: no second fetch, and the node it is bound to is
	// the same one, which is not a collision.
	if _, err := f.store.path(context.Background(), f.sim, f.action("A", 701, "/team/ivan.jpg")); err != nil {
		t.Fatalf("same address again: %v", err)
	}
	if len(f.fetched) != 1 {
		t.Errorf("fetched %v, want the address read once", f.fetched)
	}

	// The same bytes under another address: fetched (we cannot know before
	// reading), but not stored twice.
	second, err := f.store.path(context.Background(), f.sim, f.action("B", 701, "/team/ivan2.webp"))
	if err == nil {
		t.Fatalf("path() = %q, want the second node refused — one image, one subject", second)
	}
	if !strings.Contains(err.Error(), "already the picture of A") {
		t.Errorf("err = %v, want the node that already carries it named", err)
	}
	if len(f.uploads) != 1 {
		t.Errorf("uploaded %v, want the same bytes stored once", f.uploads)
	}
	if first != f.uploads[0] {
		t.Errorf("path = %q, want the storage path %q", first, f.uploads[0])
	}
}

func TestPictureRejectsWhatIsNotAPictureOfAnything(t *testing.T) {
	f := newPictureFixture(t, map[string][]byte{
		"/pixel.png": pngOf(t, 1, 1, 0),
		"/rule.png":  pngOf(t, 900, 8, 200),
		"/photo.png": pngOf(t, 300, 300, 90),
		// A wordmark: wide, and the most valuable picture a site has. The
		// proportion check has to let it through.
		"/logo.png": pngOf(t, 1260, 198, 30),
		// A favicon: small, and often the only raster picture of the site's
		// owner anywhere on it.
		"/favicon.png": pngOf(t, 48, 48, 60),
	})

	for path, want := range map[string]string{
		"/pixel.png": "1x1",
		"/rule.png":  "900x8",
		"/gone.png":  "HTTP 404",
	} {
		_, err := f.store.path(context.Background(), f.sim, f.action("A", 701, path))
		if err == nil || !strings.Contains(err.Error(), want) {
			t.Errorf("%s: err = %v, want %q in it", path, err, want)
		}
	}
	if len(f.uploads) != 0 {
		t.Errorf("uploaded %v, want nothing stored", f.uploads)
	}

	if _, err := f.store.path(context.Background(), f.sim, f.action("A", 701, "/photo.png")); err != nil {
		t.Errorf("a real photograph: %v", err)
	}
	if _, err := f.store.path(context.Background(), f.sim, f.action("B", 701, "/logo.png")); err != nil {
		t.Errorf("a wordmark logo: %v", err)
	}
	if _, err := f.store.path(context.Background(), f.sim, f.action("C", 701, "/favicon.png")); err != nil {
		t.Errorf("a favicon: %v", err)
	}
}

func TestPictureFailureIsRememberedNotRetried(t *testing.T) {
	f := newPictureFixture(t, map[string][]byte{})

	for range 3 {
		if _, err := f.store.path(context.Background(), f.sim, f.action("A", 701, "/gone.png")); err == nil {
			t.Fatal("path() = nil error for a missing image")
		}
	}
	if len(f.fetched) != 1 {
		t.Errorf("fetched %v, want one attempt — a site that refused once refuses again", f.fetched)
	}
}

func TestPictureWorkspaceFallsBackToTheForm(t *testing.T) {
	f := newPictureFixture(t, map[string][]byte{"/photo.png": pngOf(t, 300, 300, 10)})
	f.store.workspace = "" // nothing configured: ask the form the actor belongs to

	stored, err := f.store.path(context.Background(), f.sim, f.action("A", 701, "/photo.png"))
	if err != nil {
		t.Fatalf("path: %v", err)
	}
	if !strings.HasPrefix(stored, "ws-from-form/") {
		t.Errorf("stored = %q, want it uploaded into the workspace the form named", stored)
	}
}

func TestGuardedDialRefusesPrivateAddresses(t *testing.T) {
	for _, addr := range []string{"127.0.0.1:80", "10.0.0.5:443", "169.254.169.254:80", "100.100.100.200:80", "[::1]:443"} {
		if err := guardDial("tcp", addr, nil); err == nil {
			t.Errorf("guardDial(%q) = nil, want it refused", addr)
		}
	}
	if err := guardDial("tcp", "93.184.216.34:443", nil); err != nil {
		t.Errorf("guardDial(public) = %v, want nil", err)
	}
}

func TestPlanLeavesAPictureTheNodeAlreadyCarries(t *testing.T) {
	f := newWriteFixture(t)
	f.states[fixtureEmployeeA].picture = "2026/01/portrait.jpg"

	plan := f.plan(t, `
ops:
  - at: "Employee #1@aaaa"
    picture: https://acme.test/team/ivan.jpg
`)

	if len(plan.Actions) != 0 {
		t.Errorf("planned %d action(s), want none — the node has a face already", len(plan.Actions))
	}
	if len(plan.Satisfied) != 1 {
		t.Errorf("satisfied = %d, want the op reported as already done", len(plan.Satisfied))
	}
	if !strings.Contains(strings.Join(plan.Warnings, "\n"), "already carries a picture") {
		t.Errorf("warnings = %v, want the picture that was not taken reported", plan.Warnings)
	}
}

func TestApplyKeepsTheValuesWhenThePictureCannotBeTaken(t *testing.T) {
	f := newWriteFixture(t)

	// An address the fence refuses to dial: the failure every site hands out
	// sooner or later, without the test depending on one.
	res := f.apply(t, `
ops:
  - at: "ACME"
    set:
      founded_year: 1998
    picture: http://127.0.0.1:1/logo.png
`, ApplyOptions{})

	if len(res.Applied) != 1 {
		t.Fatalf("applied %d, want the op to land without its picture", len(res.Applied))
	}
	if len(res.PictureWarnings) != 1 || !strings.Contains(res.PictureWarnings[0], "ACME") {
		t.Errorf("picture warnings = %v, want the node and the reason", res.PictureWarnings)
	}
	if len(f.writes) != 1 {
		t.Fatalf("writes = %d, want one", len(f.writes))
	}
	if _, sent := f.writes[0].body["picture"]; sent {
		t.Errorf("write carried a picture key: %v", f.writes[0].body)
	}
	if data, _ := f.writes[0].body["data"].(map[string]any); data["founded_year"] == nil {
		t.Errorf("write dropped the values too: %v", f.writes[0].body)
	}
}

func TestApplyWritesNothingWhenOnlyThePictureFailed(t *testing.T) {
	f := newWriteFixture(t)

	res := f.apply(t, `
ops:
  - at: "ACME"
    picture: http://127.0.0.1:1/logo.png
`, ApplyOptions{})

	if len(res.Applied) != 0 {
		t.Errorf("applied %d, want nothing — the op had nothing else to say", len(res.Applied))
	}
	if len(f.writes) != 0 {
		t.Errorf("writes = %v, want none: an empty update would report the node as touched", f.writes)
	}
	if len(res.PictureWarnings) != 1 {
		t.Errorf("picture warnings = %v, want the one that failed", res.PictureWarnings)
	}
}
