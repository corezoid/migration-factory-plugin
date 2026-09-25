package graph

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"

	"migration-factory-plugin-mcp/internal/simulator"
)

// The fixture layer: a company, two employees sharing a title, and a folder
// whose form cannot be read.
const (
	fixtureCompany   = "11111111-1111-4111-8111-111111111111"
	fixtureEmployeeA = "aaaa1111-2222-4222-8222-222222222222"
	fixtureEmployeeB = "bbbb2222-2222-4222-8222-222222222222"
	fixtureDocs      = "33333333-3333-4333-8333-333333333333"
)

const fixtureNodes = `{"data":[
	{"id":"` + fixtureCompany + `","title":"ACME","description":" the parent\ncompany ","formId":700,"formTitle":"ACME_COMPANY","position":{"x":0,"y":0}},
	{"id":"` + fixtureEmployeeA + `","title":"Employee #1","formId":701,"formTitle":"ACME_EMPLOYEE","position":{"x":0,"y":200}},
	{"id":"` + fixtureEmployeeB + `","title":"Employee #1","formId":701,"formTitle":"ACME_EMPLOYEE","picture":"2026/09/koval.jpg","position":{"x":0,"y":100}},
	{"id":"` + fixtureDocs + `","title":"Docs","formId":702,"formTitle":"ACME_DOCS","position":{"x":0,"y":300}}
]}`

const fixtureEdges = `{"data":[
	{"id":"e1","source":"` + fixtureCompany + `","target":"` + fixtureEmployeeA + `"},
	{"id":"e2","source":"` + fixtureCompany + `","target":"` + fixtureEmployeeB + `"},
	{"id":"e3","source":"` + fixtureCompany + `","target":"` + fixtureDocs + `"}
]}`

// newFixtureSim serves the layer, the two readable forms and the actors. Form
// 702 answers 403, the way an unreadable form does.
func newFixtureSim(t *testing.T) *simulator.Client {
	t.Helper()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := strings.TrimPrefix(r.URL.Path, "/papi/1.0")
		switch {
		case strings.HasPrefix(path, "/graph_layers/paginated/"):
			if r.URL.Query().Get("type") == "edges" {
				_, _ = io.WriteString(w, fixtureEdges)
				return
			}
			_, _ = io.WriteString(w, fixtureNodes)

		case path == "/forms/700":
			_, _ = io.WriteString(w, fixtureForm700)

		case path == "/forms/701":
			_, _ = io.WriteString(w, fixtureForm701)

		case path == "/forms/702":
			w.WriteHeader(http.StatusForbidden)
			_, _ = io.WriteString(w, `{"message":"Access Denied"}`)

		case path == "/actors/"+fixtureEmployeeA:
			_, _ = io.WriteString(w, `{"data":{"id":"`+fixtureEmployeeA+`","data":{"full_name":"Ivan Petrov","item_998877":"CFO","confidence":0.9,"legacy_note":"kept verbatim"}}}`)

		case path == "/actors/"+fixtureEmployeeB:
			_, _ = io.WriteString(w, `{"data":{"id":"`+fixtureEmployeeB+`","data":{"full_name":"Olena Koval","__form__701:item_998877":[{"title":"CTO","value":"cto"}]}}}`)

		default:
			_, _ = io.WriteString(w, `{"data":{}}`)
		}
	}))
	t.Cleanup(srv.Close)

	return simulator.New(srv.URL, simulator.WithAPIKey("k3y"))
}

func exportFixture(t *testing.T) (*ExportResult, string) {
	t.Helper()
	dir := t.TempDir()
	res, err := ExportLayer(context.Background(), newFixtureSim(t), ExportOptions{LayerID: testLayerID, Dir: dir})
	if err != nil {
		t.Fatalf("ExportLayer: %v", err)
	}
	return res, dir
}

func TestExportLayerWritesTheThreeFiles(t *testing.T) {
	res, dir := exportFixture(t)

	for name, got := range map[string]string{
		ValuesFileName: res.ValuesPath,
		IDsFileName:    res.IDsPath,
		TypesFileName:  res.TypesPath,
	} {
		if want := filepath.Join(dir, name); got != want {
			t.Errorf("%s path = %q, want %q", name, got, want)
		}
		if _, err := os.Stat(got); err != nil {
			t.Errorf("%s: %v", name, err)
		}
	}

	if res.Nodes != 4 || res.Edges != 3 || res.Types != 3 || res.NodesWithValues != 2 {
		t.Errorf("result = %+v", res)
	}
	if !strings.Contains(strings.Join(res.Warnings, "\n"), "form 702") {
		t.Errorf("warnings = %v, want the unreadable form reported", res.Warnings)
	}
}

func TestExportLayerValuesFile(t *testing.T) {
	res, _ := exportFixture(t)

	raw, err := os.ReadFile(res.ValuesPath)
	if err != nil {
		t.Fatalf("read values: %v", err)
	}
	got := string(raw)

	// The field schema is not repeated here — types.schema.yaml is where a
	// name and a "[type]" resolve, and duplicating it costs more than half
	// the file on a layer whose actors are mostly empty.
	if strings.Contains(got, "\ntypes:\n") {
		t.Errorf("the values file carries a type schema again:\n%s", got)
	}
	if !strings.Contains(got, "see "+TypesFileName) {
		t.Errorf("the values file does not point at %s:\n%s", TypesFileName, got)
	}
	if head := got[:strings.Index(got, "tree: |\n")]; !strings.Contains(head, "layer: "+testLayerID+"\n") {
		t.Errorf("header = %q", head)
	}

	// Siblings follow the canvas (Employee #1@bbbb sits above @aaaa), the
	// description rides on the node line, and the values hang underneath.
	tree := got[strings.Index(got, "tree: |\n")+len("tree: |\n"):]
	tree = tree[:strings.Index(tree, "\npictures: |")]
	want := "" +
		"  ACME [company]  # the parent company\n" +
		"    Employee #1@bbbb [employee]\n" +
		"      full_name: Olena Koval\n" +
		"      role_job_title: CTO\n" +
		"    Employee #1@aaaa [employee]\n" +
		"      full_name: Ivan Petrov\n" +
		"      role_job_title: CFO\n" +
		"      confidence: 0.9\n" +
		"      legacy_note: kept verbatim\n" +
		"    Docs [docs]\n"
	if tree != want {
		t.Errorf("tree =\n%q\nwant\n%q", tree, want)
	}

	// The pictures block is what tells a run which faces are already there.
	// It is a list of paths and nothing else: the node that carries one is in
	// it, and the three that do not are not.
	pictures := got[strings.Index(got, "pictures: |\n")+len("pictures: |\n"):]
	if !strings.Contains(pictures, "  ACME > Employee #1@bbbb\n") {
		t.Errorf("pictures block does not list the node that carries one:\n%s", pictures)
	}
	if strings.Contains(pictures, "@aaaa") || strings.Contains(pictures, "Docs") {
		t.Errorf("pictures block lists a node without a picture:\n%s", pictures)
	}
}

func TestExportLayerIDsFile(t *testing.T) {
	res, _ := exportFixture(t)

	raw, err := os.ReadFile(res.IDsPath)
	if err != nil {
		t.Fatalf("read ids: %v", err)
	}
	if strings.Contains(string(raw), `\u003e`) {
		t.Errorf("the separator is HTML-escaped:\n%s", raw)
	}

	var doc struct {
		Layer string            `json:"layer"`
		Sep   string            `json:"sep"`
		Paths map[string]string `json:"paths"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("parse ids: %v", err)
	}
	if doc.Layer != testLayerID || doc.Sep != " > " {
		t.Errorf("ids header = %+v", doc)
	}
	if len(doc.Paths) != 4 {
		t.Errorf("paths = %d, want one per node", len(doc.Paths))
	}
	if doc.Paths["ACME > Employee #1@aaaa"] != fixtureEmployeeA {
		t.Errorf("paths = %v", doc.Paths)
	}
}

func TestExportLayerTypesSchema(t *testing.T) {
	res, _ := exportFixture(t)

	raw, err := os.ReadFile(res.TypesPath)
	if err != nil {
		t.Fatalf("read types: %v", err)
	}
	if !strings.Contains(string(raw), "\n  employee:\n    formId: 701\n") {
		t.Errorf("types schema is not indented two spaces per level:\n%s", raw)
	}

	var doc struct {
		Types map[string]struct {
			FormID      int    `yaml:"formId"`
			Form        string `yaml:"form"`
			Description string `yaml:"description"`
			Fields      map[string]struct {
				ID       string `yaml:"id"`
				Type     string `yaml:"type"`
				Title    string `yaml:"title"`
				Writable *bool  `yaml:"writable"`
			} `yaml:"fields"`
		} `yaml:"types"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("parse types schema: %v", err)
	}

	employee, ok := doc.Types["employee"]
	if !ok {
		t.Fatalf("types = %v, want an `employee` entry", keys(doc.Types))
	}
	if employee.FormID != 701 || employee.Form != "ACME_EMPLOYEE" || employee.Description != "One person" {
		t.Errorf("employee = %+v", employee)
	}

	for name, want := range map[string]struct{ id, ftype string }{
		"full_name":      {"full_name", TypeString},
		"role_job_title": {"item_998877", TypeString},
		"confidence":     {"confidence", TypeNumber},
		"active":         {"active", TypeBool},
		"hired_at":       {"hired_at", TypeDate},
	} {
		f, ok := employee.Fields[name]
		if !ok {
			t.Errorf("field %q is missing; got %v", name, keys(employee.Fields))
			continue
		}
		if f.ID != want.id || f.Type != want.ftype {
			t.Errorf("field %q = {id:%s type:%s}, want {id:%s type:%s}", name, f.ID, f.Type, want.id, want.ftype)
		}
	}
	if f := employee.Fields["computed_age"]; f.Writable == nil || *f.Writable {
		t.Errorf("a disabled field must be marked read-only, got %+v", f)
	}
	if company := doc.Types["company"]; company.Fields["founded_year"].Type != TypeInt {
		t.Errorf("company fields = %+v", company.Fields)
	}
	// The form behind "Docs" cannot be read, so it keeps the name the layer
	// gave it and carries no fields — a write against it fails loudly.
	if unreadable := doc.Types["docs"]; len(unreadable.Fields) != 0 || unreadable.FormID != 702 {
		t.Errorf("unreadable type = %+v", unreadable)
	}
}

func TestExportLayerIsDeterministic(t *testing.T) {
	first, _ := exportFixture(t)
	second, _ := exportFixture(t)

	for _, pair := range [][2]string{
		{first.ValuesPath, second.ValuesPath},
		{first.IDsPath, second.IDsPath},
		{first.TypesPath, second.TypesPath},
	} {
		a, err := os.ReadFile(pair[0])
		if err != nil {
			t.Fatalf("read %s: %v", pair[0], err)
		}
		b, err := os.ReadFile(pair[1])
		if err != nil {
			t.Fatalf("read %s: %v", pair[1], err)
		}
		if string(a) != string(b) {
			t.Errorf("%s differs between two exports of the same layer", filepath.Base(pair[0]))
		}
	}
}

// An empty layer is almost always the wrong layer id or a key for another
// workspace. Exporting it anyway would leave `paths: {}` and `types: {}`
// behind, and every apply and listing in that directory would then fail
// blaming the files rather than the export.
func TestExportLayerRefusesAnEmptyLayer(t *testing.T) {
	dir := t.TempDir()
	sim := newTestSim(t, `{"data":[]}`, `{"data":[]}`)

	_, err := ExportLayer(context.Background(), sim, ExportOptions{LayerID: testLayerID, Dir: dir})
	if err == nil {
		t.Fatal("an empty layer was exported")
	}
	if !strings.Contains(err.Error(), testLayerID) || !strings.Contains(err.Error(), "no nodes") {
		t.Errorf("error = %v, want the layer named and the emptiness said", err)
	}

	entries, readErr := os.ReadDir(dir)
	if readErr != nil {
		t.Fatalf("read the output directory: %v", readErr)
	}
	if len(entries) != 0 {
		t.Errorf("the refused export still wrote %d file(s)", len(entries))
	}
}

func TestExportLayerWritesNothingElse(t *testing.T) {
	_, dir := exportFixture(t)

	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("read the output directory: %v", err)
	}
	var got []string
	for _, e := range entries {
		got = append(got, e.Name())
	}
	sort.Strings(got)

	want := []string{IDsFileName, TypesFileName, ValuesFileName}
	sort.Strings(want)
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("directory = %v, want exactly %v (no raw layer, no map)", got, want)
	}
}

func keys[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
