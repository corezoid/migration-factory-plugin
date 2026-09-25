package graph

import (
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"migration-factory-plugin-mcp/internal/simulator"
)

const testLayerID = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"

// newTestSim serves the two layer listings a pull makes.
func newTestSim(t *testing.T, nodes, edges string) *simulator.Client {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if want := "/papi/1.0/graph_layers/paginated/" + testLayerID; r.URL.Path != want {
			t.Errorf("path = %q, want %q", r.URL.Path, want)
		}
		switch r.URL.Query().Get("type") {
		case "nodes":
			_, _ = io.WriteString(w, nodes)
		case "edges":
			_, _ = io.WriteString(w, edges)
		default:
			t.Errorf("unexpected type = %q", r.URL.Query().Get("type"))
		}
	}))
	t.Cleanup(srv.Close)
	return simulator.New(srv.URL, simulator.WithAPIKey("k3y"))
}
