package simulator

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// A layer route that ignores offset serves its first page forever. The walk
// ends in an error at the cap, not in a spin that outlives the export.
func TestListLayerActorsStopsWhenTheRouteDoesNotPage(t *testing.T) {
	calls := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		rows := make([]string, 0, layerPageLimit)
		for i := range layerPageLimit {
			rows = append(rows, fmt.Sprintf(`{"id":"node-%d","title":"Node %d","position":{"x":%d,"y":0}}`, i, i, i))
		}
		_, _ = io.WriteString(w, `{"data":[`+strings.Join(rows, ",")+`]}`)
	}))
	t.Cleanup(srv.Close)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	_, err := New(srv.URL, WithAPIKey("k")).ListLayerActors(ctx, "371614ee-912b-4802-93df-25d6d566b2df")
	if err == nil || !strings.Contains(err.Error(), "not paging") {
		t.Fatalf("err = %v, want the walk refused as not paging", err)
	}
	if calls != maxLayerPages {
		t.Errorf("requests = %d, want exactly the cap of %d", calls, maxLayerPages)
	}
}
