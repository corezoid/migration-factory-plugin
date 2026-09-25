package graph

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"

	"migration-factory-plugin-mcp/internal/simulator"
)

// actorState is what the fixture serves for one actor and what a write
// against it is checked against.
type actorState struct {
	title       string
	description string
	formID      int
	hole        bool
	ref         string
	picture     string
	data        string // raw JSON object
}

// write is one request the fixture recorded.
type write struct {
	path  string
	query string
	body  map[string]any
}

// share is one access-rules POST the fixture recorded: the body is a list of
// rules, not one object, so it has a shape of its own.
type share struct {
	path  string
	query string
	rules []map[string]any
}

// The two readable forms of the fixture layer, shared with the export test.
const fixtureForm700 = `{"data":{"id":700,"title":"ACME_COMPANY","description":"A company","form":{"sections":[
	{"title":"Main","content":[
		{"id":"name","class":"edit","type":"text","title":"Company name"},
		{"id":"founded_year","class":"edit","type":"int","title":"Founded year"}
	]}
]}}}`

const fixtureForm701 = `{"data":{"id":701,"title":"ACME_EMPLOYEE","description":"One person","form":{"sections":[
	{"title":"Person","content":[
		{"id":"full_name","class":"edit","type":"text","title":"Full name"},
		{"id":"item_998877","class":"edit","type":"text","title":"Role / job title — what they do"},
		{"id":"confidence","class":"edit","type":"float","title":"Extraction confidence 0..1"},
		{"id":"active","class":"check","title":"Currently employed"},
		{"id":"hired_at","class":"calendar","title":"Hire date"},
		{"id":"computed_age","class":"edit","type":"text","title":"Age","visibility":"disabled"}
	]}
]}}}`

// writeFixture serves the same layer as the export fixture, plus the actor
// reads the planner makes and the updates an apply sends.
type writeFixture struct {
	sim    *simulator.Client
	mu     sync.Mutex
	writes []write
	// The request counts a run makes, per kind: what the export beside the
	// ops file is there to avoid, and what it cannot.
	formReads  int
	layerReads int
	actorReads int
	states     map[string]*actorState
	// creates are the POSTs a run made, in order, and refs is the records
	// they left behind: "<formId>/<ref>" -> actor id, which is how the
	// gateway answers a read by business key.
	creates []write
	refs    map[string]string
	minted  int
	// shares are the access-rule POSTs a run made; shareStatus, when set,
	// is what the gateway answers them with instead of 200.
	shares      []share
	shareStatus int
	// plannedRefMiss makes the next read of one ref answer 404 even though
	// the record is there: the shape of losing a race between the plan and
	// the create.
	plannedRefMiss string
	// nodes is the layer listing, mutable so a test can show the layer as it
	// reads after a write landed.
	nodes string
}

// reset zeroes the counters so a test can measure one run.
func (f *writeFixture) reset() {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.formReads, f.layerReads, f.actorReads, f.writes = 0, 0, 0, nil
	f.creates = nil
}

func (f *writeFixture) count(kind *int) {
	f.mu.Lock()
	*kind++
	f.mu.Unlock()
}

func newWriteFixture(t *testing.T) *writeFixture {
	t.Helper()

	f := &writeFixture{states: map[string]*actorState{
		fixtureCompany: {title: "ACME", formID: 700, data: `{"name":"ACME"}`},
		fixtureEmployeeA: {title: "Employee #1", formID: 701,
			data: `{"full_name":"Ivan Petrov","item_998877":"CFO","confidence":0.9,"legacy_note":"kept verbatim"}`},
		// A multiform node: its own values are keyed under the leaf form, and
		// that is the form the write has to address it by.
		fixtureEmployeeB: {title: "Employee #1", formID: 700,
			data: `{"full_name":"Olena Koval","__form__701:item_998877":[{"title":"CTO","value":"cto"}]}`},
		fixtureDocs: {title: "Docs", formID: 702, data: `{}`},
	}, refs: map[string]string{}, nodes: fixtureNodes}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := strings.TrimPrefix(r.URL.Path, "/papi/1.0")

		if r.Method == http.MethodPut {
			var body map[string]any
			raw, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(raw, &body)
			f.mu.Lock()
			f.writes = append(f.writes, write{path: path, query: r.URL.RawQuery, body: body})
			// The fixture serves what it was set up with, not what was
			// written — except the ref, which is folded back in because it
			// is the address a later file finds the record by, and a test
			// of that second lookup has to see the first write land.
			if ref, _ := body["ref"].(string); ref != "" {
				formID, actorID, _ := strings.Cut(strings.TrimPrefix(path, "/actors/actor/"), "/")
				if state := f.states[actorID]; state != nil {
					state.ref = ref
					f.refs[formID+"/"+ref] = actorID
				}
			}
			f.mu.Unlock()
			_, _ = io.WriteString(w, `{"data":{}}`)
			return
		}

		if r.Method == http.MethodPost && strings.HasPrefix(path, "/access_rules/actor/") {
			var rules []map[string]any
			raw, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(raw, &rules)
			f.mu.Lock()
			f.shares = append(f.shares, share{path: path, query: r.URL.RawQuery, rules: rules})
			status := f.shareStatus
			f.mu.Unlock()
			if status != 0 {
				w.WriteHeader(status)
				_, _ = io.WriteString(w, `{"message":"access rules unavailable"}`)
				return
			}
			_, _ = io.WriteString(w, `{"data":[]}`)
			return
		}

		if r.Method == http.MethodPost && strings.HasPrefix(path, "/actors/actor/") {
			var body map[string]any
			raw, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(raw, &body)
			formID := strings.TrimPrefix(path, "/actors/actor/")

			f.mu.Lock()
			f.creates = append(f.creates, write{path: path, query: r.URL.RawQuery, body: body})
			ref, _ := body["ref"].(string)
			key := formID + "/" + ref
			if _, taken := f.refs[key]; taken {
				f.mu.Unlock()
				w.WriteHeader(http.StatusConflict)
				_, _ = io.WriteString(w, `{"message":"ref already exists"}`)
				return
			}
			f.minted++
			id := fmt.Sprintf("%08d-0000-4000-8000-000000000000", f.minted)
			title, _ := body["title"].(string)
			description, _ := body["description"].(string)
			data, _ := json.Marshal(body["data"])
			form, _ := strconv.Atoi(formID)
			f.states[id] = &actorState{title: title, description: description, formID: form, ref: ref, data: string(data)}
			f.refs[key] = id
			f.mu.Unlock()

			_, _ = io.WriteString(w, `{"data":{"id":"`+id+`"}}`)
			return
		}

		switch {
		case strings.HasPrefix(path, "/actors/ref/"):
			f.count(&f.actorReads)
			key := strings.TrimPrefix(path, "/actors/ref/")
			f.mu.Lock()
			id := f.refs[key]
			if f.plannedRefMiss == key {
				f.plannedRefMiss, id = "", ""
			}
			state := f.states[id]
			f.mu.Unlock()
			if state == nil {
				w.WriteHeader(http.StatusNotFound)
				_, _ = io.WriteString(w, `{"message":"no such actor"}`)
				return
			}
			_, _ = io.WriteString(w, f.actorJSON(id, state))

		case strings.HasPrefix(path, "/graph_layers/paginated/"):
			f.count(&f.layerReads)
			if r.URL.Query().Get("type") == "edges" {
				_, _ = io.WriteString(w, fixtureEdges)
				return
			}
			f.mu.Lock()
			nodes := f.nodes
			f.mu.Unlock()
			_, _ = io.WriteString(w, nodes)

		case path == "/forms/700":
			f.count(&f.formReads)
			_, _ = io.WriteString(w, fixtureForm700)
		case path == "/forms/701":
			f.count(&f.formReads)
			_, _ = io.WriteString(w, fixtureForm701)
		case path == "/forms/702":
			f.count(&f.formReads)
			w.WriteHeader(http.StatusForbidden)
			_, _ = io.WriteString(w, `{"message":"Access Denied"}`)

		case strings.HasPrefix(path, "/actors/"):
			id := strings.TrimPrefix(path, "/actors/")
			f.count(&f.actorReads)
			f.mu.Lock()
			state := f.states[id]
			f.mu.Unlock()
			if state == nil {
				w.WriteHeader(http.StatusNotFound)
				_, _ = io.WriteString(w, `{"message":"no such actor"}`)
				return
			}
			_, _ = io.WriteString(w, f.actorJSON(id, state))

		default:
			_, _ = io.WriteString(w, `{"data":{}}`)
		}
	}))
	t.Cleanup(srv.Close)

	f.sim = simulator.New(srv.URL, simulator.WithAPIKey("k3y"))
	return f
}

// actorJSON renders one actor the way a read returns it.
func (f *writeFixture) actorJSON(id string, state *actorState) string {
	return `{"data":{"id":"` + id + `","title":` + quote(state.title) +
		`,"description":` + quote(state.description) +
		`,"ref":` + quote(state.ref) +
		`,"picture":` + quote(state.picture) +
		`,"formId":` + strconv.Itoa(state.formID) +
		`,"hole":` + boolJSON(state.hole) + `,"data":` + state.data + `}}`
}

// renameFixtureNode rewrites the layer listing so the node answers to its new
// title, the way the layer reads after a rename lands.
func (f *writeFixture) renameFixtureNode(id, title string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.nodes = strings.Replace(f.nodes, `"id":"`+id+`","title":"Docs"`, `"id":"`+id+`","title":"`+title+`"`, 1)
}

func quote(s string) string {
	raw, _ := json.Marshal(s)
	return string(raw)
}

func boolJSON(b bool) string {
	if b {
		return "true"
	}
	return "false"
}

// plan runs an ops file against the fixture without writing.
func (f *writeFixture) plan(t *testing.T, ops string) *Plan {
	t.Helper()
	res := f.apply(t, ops, ApplyOptions{DryRun: true})
	return res.Plan
}

func (f *writeFixture) apply(t *testing.T, ops string, opts ApplyOptions) *ApplyResult {
	t.Helper()
	doc, err := ParseOps([]byte(ops))
	if err != nil {
		t.Fatalf("ParseOps: %v", err)
	}
	if opts.LayerID == "" {
		opts.LayerID = testLayerID
	}
	res, err := ApplyOps(context.Background(), f.sim, doc, opts)
	if err != nil && res == nil {
		t.Fatalf("ApplyOps: %v", err)
	}
	return res
}

// errorFor returns the message of the failure for op n.
func (p *Plan) errorFor(n int) string {
	for _, e := range p.Errors {
		if e.OpIndex == n {
			return e.Err.Error()
		}
	}
	return ""
}

func TestPlanOpsSkipsValuesTheLayerAlreadyHolds(t *testing.T) {
	f := newWriteFixture(t)

	plan := f.plan(t, `
ops:
  - at: "Employee #1@aaaa"
    set:
      full_name: Ivan Petrov
      role_job_title: CFO
  - at: "ACME > Docs"
    rename: Docs
`)

	if len(plan.Errors) != 0 {
		t.Fatalf("errors: %v", plan.Errors[0].Err)
	}
	if len(plan.Actions) != 0 {
		t.Errorf("planned %d actions, want none — every value is already stored", len(plan.Actions))
	}
	if len(plan.Satisfied) != 2 {
		t.Errorf("satisfied = %d, want both ops", len(plan.Satisfied))
	}
}

func TestPlanOpsDiffsOnlyTheFieldsThatChange(t *testing.T) {
	f := newWriteFixture(t)

	plan := f.plan(t, `
ops:
  - at: "Employee #1@aaaa"
    set:
      full_name: Ivan Petrov
      role_job_title: CEO
      hired_at: 2024-03-01
      active: true
`)

	if len(plan.Errors) != 0 {
		t.Fatalf("errors: %v", plan.Errors[0].Err)
	}
	if len(plan.Actions) != 1 {
		t.Fatalf("planned %d actions, want 1", len(plan.Actions))
	}

	a := plan.Actions[0]
	got := map[string]any{}
	for _, c := range a.Changes {
		got[c.Field] = c.Value
	}
	if len(got) != 3 {
		t.Errorf("changes = %v, want the unchanged full_name left out", got)
	}
	if got["role_job_title"] != "CEO" || got["active"] != true {
		t.Errorf("changes = %v", got)
	}
	// An unquoted YAML date arrives as a time.Time and has to be written as
	// the day it names, not as an RFC 3339 instant.
	if got["hired_at"] != "2024-03-01" {
		t.Errorf("hired_at = %#v, want the normalised day", got["hired_at"])
	}
	// The values are keyed by the Simulator field id, not by the schema name.
	if _, ok := a.Data()["item_998877"]; !ok {
		t.Errorf("data = %v, want the role keyed by its field id", a.Data())
	}
}

// A multiform node keys another form's fields as "__form__<id>:<field>", and
// a write has to land on that key rather than beside it.
func TestPlanOpsKeepsTheMultiformKey(t *testing.T) {
	f := newWriteFixture(t)

	plan := f.plan(t, `
ops:
  - at: "Employee #1@bbbb"
    set:
      role_job_title: CEO
`)

	if len(plan.Actions) != 1 {
		t.Fatalf("planned %d actions: %s", len(plan.Actions), plan.Render())
	}
	if key := plan.Actions[0].Changes[0].Key; key != "__form__701:item_998877" {
		t.Errorf("key = %q, want the stored multiform key", key)
	}
	// The node's own formId names the root form; the leaf its values are
	// keyed under is what the update route takes.
	if got := plan.Actions[0].FormID; got != 701 {
		t.Errorf("formId = %d, want the leaf form 701", got)
	}
	if old := plan.Actions[0].Changes[0].Old; old != "CTO" {
		t.Errorf("old = %q, want the title of the stored option", old)
	}
}

func TestPlanOpsRejectsWhatItCannotApply(t *testing.T) {
	f := newWriteFixture(t)

	plan := f.plan(t, `
ops:
  - at: "Employee #1"
    set: {full_name: X}
  - at: "Employee #1@aaaa"
    set: {ful_name: X}
  - at: "Employee #1@aaaa"
    set: {computed_age: "41"}
  - at: "Employee #1@aaaa"
    set: {title: Renamed}
  - at: "Employee #1@aaaa"
    set: {confidence: high}
  - at: "ACME > Docs"
    set: {anything: X}
  - under: "ACME"
    create: "Employee #9"
    type: employee
  - at: "Employee #1@aaaa"
    append: {full_name: " Jr"}
  - at: "Nowhere"
    set: {full_name: X}
`)

	for n, want := range map[int]string{
		1: "ambiguous address",
		2: `no field "ful_name" (did you mean "full_name"?)`,
		3: `field "computed_age" is not writable`,
		4: "do not write `title`",
		5: `"high" is not a number`,
		6: "form 702 is not in",
		7: "`under:`/`create:` is not supported",
		8: "`append:` is not supported",
		9: "no node matches",
	} {
		if got := plan.errorFor(n); !strings.Contains(got, want) {
			t.Errorf("op #%d error = %q, want it to mention %q", n, got, want)
		}
	}
	if len(plan.Actions) != 0 {
		t.Errorf("planned %d actions from a file of errors", len(plan.Actions))
	}
}

// Every complaint about one op is reported together: fixing one typo per run
// is the slowest possible way to find out about the other three.
func TestPlanOpsReportsEveryProblemInAnOpAtOnce(t *testing.T) {
	f := newWriteFixture(t)

	plan := f.plan(t, `
ops:
  - at: "Employee #1@aaaa"
    set:
      confidence: high
      ful_name: X
      computed_age: "41"
`)

	got := plan.errorFor(1)
	for _, want := range []string{
		`field "confidence": "high" is not a number`,
		`field "computed_age" is not writable`,
		`no field "ful_name"`,
	} {
		if !strings.Contains(got, want) {
			t.Errorf("error = %q, want it to mention %q", got, want)
		}
	}
}

func TestApplyOpsWritesOnlyTheChangedFields(t *testing.T) {
	f := newWriteFixture(t)

	res := f.apply(t, `
ops:
  - at: "Employee #1@aaaa"
    set:
      full_name: Ivan Petrov
      role_job_title: CEO
    rename: "Employee #1 — CEO"
`, ApplyOptions{})

	if len(res.Applied) != 1 || len(res.Failed) != 0 {
		t.Fatalf("applied %d, failed %d: %s", len(res.Applied), len(res.Failed), res.Plan.Render())
	}
	if len(f.writes) != 1 {
		t.Fatalf("sent %d writes, want one per node", len(f.writes))
	}

	w := f.writes[0]
	if want := "/actors/actor/701/" + fixtureEmployeeA; w.path != want {
		t.Errorf("path = %q, want %q", w.path, want)
	}
	// Without replaceEmpty=false the route takes the backend default, and a
	// partial write cannot depend on that.
	if w.query != "replaceEmpty=false" {
		t.Errorf("query = %q, want replaceEmpty=false", w.query)
	}
	if w.body["title"] != "Employee #1 — CEO" {
		t.Errorf("title = %v", w.body["title"])
	}
	data, _ := w.body["data"].(map[string]any)
	if len(data) != 1 || data["item_998877"] != "CEO" {
		t.Errorf("data = %v, want only the changed field", data)
	}
	if _, sent := w.body["hole"]; sent {
		t.Error("hole was sent for a node that is not a placeholder")
	}
}

// A hole is a node that already sits on the canvas with its edges and holds
// nothing. Writing into it is the one way this version adds data to a node
// that has none, so the write has to say the slot is no longer empty.
func TestApplyOpsFillsAHole(t *testing.T) {
	f := newWriteFixture(t)
	f.states[fixtureDocs] = &actorState{title: "Docs", formID: 702, hole: true, data: `{}`}
	f.states[fixtureCompany].hole = true

	res := f.apply(t, `
ops:
  - at: "ACME"
    set: {name: ACME Holding}
`, ApplyOptions{})

	if len(res.Applied) != 1 {
		t.Fatalf("applied %d: %s", len(res.Applied), res.Plan.Render())
	}
	if !res.Plan.Actions[0].FillsHole {
		t.Error("the action does not report that it fills a hole")
	}
	if got := f.writes[0].body["hole"]; got != false {
		t.Errorf("hole = %v, want an explicit false", got)
	}
	if !strings.Contains(res.Plan.Render(), "fills a placeholder hole") {
		t.Errorf("plan does not mention the hole:\n%s", res.Plan.Render())
	}
}

// Any write at all ends a placeholder: once someone has named it, it is a
// node somebody is working on rather than a slot nobody has claimed.
func TestApplyOpsFillsAHoleOnARenameToo(t *testing.T) {
	f := newWriteFixture(t)
	f.states[fixtureDocs] = &actorState{title: "Docs", formID: 702, hole: true, data: `{}`}

	res := f.apply(t, `
ops:
  - at: "ACME > Docs"
    rename: Documents
`, ApplyOptions{})

	if len(res.Applied) != 1 {
		t.Fatalf("applied %d: %s", len(res.Applied), res.Plan.Render())
	}
	if !res.Plan.Actions[0].FillsHole {
		t.Error("a rename did not end the placeholder")
	}
	if got := f.writes[0].body["hole"]; got != false {
		t.Errorf("hole = %v, want an explicit false", got)
	}
}

// An op the layer already agrees with writes nothing, so it leaves the flag
// as it found it — the check is about writing, not about being addressed.
func TestApplyOpsLeavesAHoleAloneWhenNothingIsWritten(t *testing.T) {
	f := newWriteFixture(t)
	f.states[fixtureDocs] = &actorState{title: "Docs", formID: 702, hole: true, data: `{}`}

	res := f.apply(t, `
ops:
  - at: "ACME > Docs"
    rename: Docs
`, ApplyOptions{})

	if len(res.Applied) != 0 || len(res.Plan.Satisfied) != 1 {
		t.Errorf("applied %d, satisfied %d", len(res.Applied), len(res.Plan.Satisfied))
	}
	if len(f.writes) != 0 {
		t.Errorf("sent %d writes for an op that changes nothing", len(f.writes))
	}
}

func TestApplyOpsDryRunWritesNothing(t *testing.T) {
	f := newWriteFixture(t)

	res := f.apply(t, `
ops:
  - at: "Employee #1@aaaa"
    set: {full_name: Someone Else}
`, ApplyOptions{DryRun: true})

	if len(res.Plan.Actions) != 1 {
		t.Fatalf("planned %d actions", len(res.Plan.Actions))
	}
	if len(f.writes) != 0 {
		t.Errorf("a dry run sent %d writes", len(f.writes))
	}
	if len(res.Applied) != 0 {
		t.Errorf("a dry run applied %d actions", len(res.Applied))
	}
}

// One bad op stops the whole file: half an imported document leaves a twin
// nobody can reason about, and there is nothing to roll back to.
func TestApplyOpsRefusesAFileWithErrors(t *testing.T) {
	f := newWriteFixture(t)

	res := f.apply(t, `
ops:
  - at: "Employee #1@aaaa"
    set: {full_name: Someone Else}
  - at: "Employee #1@aaaa"
    set: {ful_name: X}
`, ApplyOptions{})

	if len(f.writes) != 0 {
		t.Errorf("wrote %d times despite an invalid op", len(f.writes))
	}
	if len(res.Plan.Actions) != 1 || len(res.Plan.Errors) != 1 {
		t.Errorf("plan = %d actions, %d errors", len(res.Plan.Actions), len(res.Plan.Errors))
	}
}

func TestApplyOpsChecksTheLayer(t *testing.T) {
	f := newWriteFixture(t)

	doc, err := ParseOps([]byte("layer: 99999999-9999-4999-8999-999999999999\nops: []\n"))
	if err != nil {
		t.Fatalf("ParseOps: %v", err)
	}
	if _, err := ApplyOps(context.Background(), f.sim, doc, ApplyOptions{LayerID: testLayerID}); err == nil {
		t.Fatal("an ops file aimed at another layer was accepted")
	}
}

// Replaying an ops file is the supported way to re-import a document: the
// second run finds every value already stored and writes nothing.
func TestApplyOpsIsReplayable(t *testing.T) {
	f := newWriteFixture(t)

	const ops = `
ops:
  - at: "Employee #1@aaaa"
    set: {role_job_title: CEO}
`
	if res := f.apply(t, ops, ApplyOptions{}); len(res.Applied) != 1 {
		t.Fatalf("first run applied %d", len(res.Applied))
	}
	// The fixture does not store what it is sent, so the second run is made
	// to see the applied state the way Simulator would serve it.
	f.states[fixtureEmployeeA].data = `{"full_name":"Ivan Petrov","item_998877":"CEO","confidence":0.9}`

	res := f.apply(t, ops, ApplyOptions{})
	if len(res.Applied) != 0 || len(res.Plan.Satisfied) != 1 {
		t.Errorf("second run applied %d, satisfied %d — a replay must write nothing",
			len(res.Applied), len(res.Plan.Satisfied))
	}
	if len(f.writes) != 1 {
		t.Errorf("two runs sent %d writes", len(f.writes))
	}
}

// The schema file an export writes has to read back as the dictionary that
// wrote it — it is the write path's only source of field ids when an apply
// takes its types from the export instead of the form API.
func TestTypesSchemaRoundTrips(t *testing.T) {
	idx, err := LoadIndex(context.Background(), newWriteFixture(t).sim, testLayerID, "", 0)
	if err != nil {
		t.Fatalf("LoadIndex: %v", err)
	}
	rendered, err := RenderTypesSchema(testLayerID, idx.Types)
	if err != nil {
		t.Fatalf("RenderTypesSchema: %v", err)
	}
	parsed, err := ParseTypesSchema(rendered)
	if err != nil {
		t.Fatalf("ParseTypesSchema: %v", err)
	}

	for _, want := range idx.Types.Types {
		got, ok := parsed.Type(want.FormID)
		if !ok {
			t.Errorf("form %d missing after the round trip", want.FormID)
			continue
		}
		if got.Slug != want.Slug || len(got.Fields) != len(want.Fields) {
			t.Errorf("form %d = %q with %d fields, want %q with %d",
				want.FormID, got.Slug, len(got.Fields), want.Slug, len(want.Fields))
		}
		for _, f := range want.Fields {
			back, ok := got.Field(f.ID)
			if !ok {
				t.Errorf("%s.%s (id %s) missing after the round trip", want.Slug, f.Name, f.ID)
				continue
			}
			if back.Name != f.Name || back.Type != f.Type || back.Writable != f.Writable {
				t.Errorf("%s.%s = %+v, want %+v", want.Slug, f.Name, back, f)
			}
		}
	}
}

// A node whose form the exported schema does not describe fails by name,
// pointing at the file to re-export rather than at the field.
func TestPlanOpsNamesTheFormMissingFromTheSchemaFile(t *testing.T) {
	f := newWriteFixture(t)
	dir := t.TempDir()

	// A dictionary that knows only the employee form, as an export taken
	// before the company node was added would be.
	write := func(name, body string) {
		t.Helper()
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o600); err != nil {
			t.Fatalf("write %s: %v", name, err)
		}
	}
	write(TypesFileName, `
types:
  employee:
    formId: 701
    fields:
      full_name: {id: full_name, type: string}
`)
	write(IDsFileName, `{"layer":"`+testLayerID+`","sep":" > ","paths":{
		"ACME": "`+fixtureCompany+`",
		"ACME > Employee #1@aaaa": "`+fixtureEmployeeA+`"}}`)
	write(OpsFileName, `
layer: `+testLayerID+`
ops:
  - at: "ACME"
    set: {name: ACME Holding}
`)

	res, err := ApplyOpsFile(context.Background(), f.sim, filepath.Join(dir, OpsFileName), ApplyOptions{})
	if err == nil {
		t.Fatal("an op against a form the schema file does not describe was applied")
	}
	got := res.Plan.errorFor(1)
	for _, want := range []string{"form 700", TypesFileName, "re-export"} {
		if !strings.Contains(got, want) {
			t.Errorf("error = %q, want %q named", got, want)
		}
	}
}

// With both sidecars beside the ops file, neither the layer nor the forms are
// read: the paths come from graph.ids.json and the schemas from
// types.schema.yaml, and only the addressed node is fetched.
func TestApplyOpsReadsNeitherLayerNorFormsWhenTheExportIsBesideIt(t *testing.T) {
	f := newWriteFixture(t)
	dir := t.TempDir()

	idx, err := LoadIndex(context.Background(), f.sim, testLayerID, "", 0)
	if err != nil {
		t.Fatalf("LoadIndex: %v", err)
	}
	schema, err := RenderTypesSchema(testLayerID, idx.Types)
	if err != nil {
		t.Fatalf("RenderTypesSchema: %v", err)
	}
	tree, _, err := BuildTree(File{LayerID: testLayerID, Actors: fixtureActors(t), Edges: fixtureEdgeList()})
	if err != nil {
		t.Fatalf("BuildTree: %v", err)
	}
	ids, err := RenderIDs(tree)
	if err != nil {
		t.Fatalf("RenderIDs: %v", err)
	}
	for name, body := range map[string][]byte{TypesFileName: schema, IDsFileName: ids} {
		if err := os.WriteFile(filepath.Join(dir, name), body, 0o600); err != nil {
			t.Fatalf("write %s: %v", name, err)
		}
	}
	opsPath := filepath.Join(dir, OpsFileName)
	if err := os.WriteFile(opsPath, []byte(`
layer: `+testLayerID+`
ops:
  - at: "Employee #1@aaaa"
    set: {role_job_title: CEO}
`), 0o600); err != nil {
		t.Fatalf("write ops: %v", err)
	}

	// KeepExport measures the apply alone: the refresh that normally follows
	// a write is a full export and reads everything by design.
	f.reset()
	res, err := ApplyOpsFile(context.Background(), f.sim, opsPath, ApplyOptions{KeepExport: true})
	if err != nil {
		t.Fatalf("ApplyOpsFile: %v", err)
	}

	if f.formReads != 0 || f.layerReads != 0 {
		t.Errorf("read %d forms and %d layer pages, want none of either", f.formReads, f.layerReads)
	}
	if f.actorReads != 1 {
		t.Errorf("read %d actors, want only the addressed one", f.actorReads)
	}
	if len(res.Applied) != 1 {
		t.Fatalf("applied %d: %s", len(res.Applied), res.Plan.Render())
	}
	if _, ok := res.Applied[0].Data()["item_998877"]; !ok {
		t.Errorf("data = %v, want the id from the schema file", res.Applied[0].Data())
	}
	for _, want := range []string{IDsFileName, TypesFileName} {
		if !strings.Contains(res.Plan.Render(), want) {
			t.Errorf("the plan does not say it read %s:\n%s", want, res.Plan.Render())
		}
	}
}

// The ids sidecar an export writes has to read back as the map that wrote it.
func TestIDsRoundTrip(t *testing.T) {
	tree, _, err := BuildTree(File{LayerID: testLayerID, Actors: fixtureActors(t), Edges: fixtureEdgeList()})
	if err != nil {
		t.Fatalf("BuildTree: %v", err)
	}
	rendered, err := RenderIDs(tree)
	if err != nil {
		t.Fatalf("RenderIDs: %v", err)
	}
	layerID, paths, err := ParseIDs(rendered)
	if err != nil {
		t.Fatalf("ParseIDs: %v", err)
	}

	if layerID != testLayerID || paths.Len() != len(tree.Nodes) {
		t.Fatalf("layer %q with %d paths, want %q with %d", layerID, paths.Len(), testLayerID, len(tree.Nodes))
	}
	for _, n := range tree.Nodes {
		got, err := paths.Resolve(n.Path)
		if err != nil || got.ID != n.ID() {
			t.Errorf("Resolve(%q) = %+v, %v, want %s", n.Path, got, err, n.ID())
		}
	}
	// Ambiguity has to survive the file too: it is a property of the path
	// set, and the file holds the whole set.
	if _, err := paths.Resolve("Employee #1"); err == nil || !strings.Contains(err.Error(), "ambiguous") {
		t.Errorf("Resolve(ambiguous) = %v, want both candidates listed", err)
	}
}

// fixtureActors and fixtureEdgeList decode the fixture layer the way a pull
// does, so a test can build the tree without a server.
func fixtureActors(t *testing.T) []Actor {
	t.Helper()
	var page struct {
		Data []struct {
			ID       string             `json:"id"`
			Title    string             `json:"title"`
			FormID   int                `json:"formId"`
			Position struct{ X, Y int } `json:"position"`
		} `json:"data"`
	}
	if err := json.Unmarshal([]byte(fixtureNodes), &page); err != nil {
		t.Fatalf("decode fixture nodes: %v", err)
	}
	actors := make([]Actor, 0, len(page.Data))
	for _, a := range page.Data {
		actors = append(actors, Actor{ID: a.ID, Title: a.Title, FormID: a.FormID,
			Position: Position{X: a.Position.X, Y: a.Position.Y}})
	}
	return actors
}

func fixtureEdgeList() []Edge {
	return []Edge{
		{Source: fixtureCompany, Target: fixtureEmployeeA},
		{Source: fixtureCompany, Target: fixtureEmployeeB},
		{Source: fixtureCompany, Target: fixtureDocs},
	}
}

// The index may be a snapshot, and this is the drift worth noticing: the node
// the path names is no longer titled what the export recorded. The uuid still
// names the right node, so it is a warning and not an error — but it is the
// visible sign that someone else has been editing the layer.
func TestPlanOpsWarnsWhenTheIndexIsOlderThanTheNode(t *testing.T) {
	f := newWriteFixture(t)
	f.states[fixtureEmployeeA].title = "Employee #1 — CFO"

	res := f.apply(t, `
ops:
  - at: "Employee #1@aaaa"
    set: {role_job_title: CEO}
`, ApplyOptions{DryRun: true})

	joined := strings.Join(res.Plan.Warnings, "\n")
	if !strings.Contains(joined, "Employee #1 — CFO") || !strings.Contains(joined, "older than the node") {
		t.Errorf("warnings = %q, want the drift reported", joined)
	}
	if len(res.Plan.Actions) != 1 {
		t.Errorf("the warning stopped the op: %s", res.Plan.Render())
	}
}

// A write makes the export beside the ops file wrong — it is the snapshot the
// next run resolves its addresses against — so a successful run rewrites it.
func TestApplyOpsRefreshesTheExportItResolvedAgainst(t *testing.T) {
	f := newWriteFixture(t)
	dir := exportFixtureDir(t, f)
	opsPath := filepath.Join(dir, OpsFileName)
	if err := os.WriteFile(opsPath, []byte(`
layer: `+testLayerID+`
ops:
  - at: "Employee #1@aaaa"
    set: {role_job_title: CEO}
`), 0o600); err != nil {
		t.Fatalf("write ops: %v", err)
	}

	// Make the sidecars visibly stale, so a refresh is the only thing that
	// could put them back.
	for _, name := range []string{IDsFileName, TypesFileName, ValuesFileName} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte("stale\n"), 0o600); err != nil {
			t.Fatalf("stale %s: %v", name, err)
		}
	}
	// graph.ids.json is read before the write, so restore the one the plan
	// needs and leave the other two broken.
	writeFixtureIDs(t, dir)
	writeFixtureTypes(t, f, dir)

	res, err := ApplyOpsFile(context.Background(), f.sim, opsPath, ApplyOptions{})
	if err != nil {
		t.Fatalf("ApplyOpsFile: %v", err)
	}
	if res.Export == nil {
		t.Fatal("the export was not refreshed")
	}
	for _, name := range []string{IDsFileName, TypesFileName, ValuesFileName} {
		raw, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil {
			t.Fatalf("read %s: %v", name, err)
		}
		if strings.HasPrefix(string(raw), "stale") {
			t.Errorf("%s was not rewritten", name)
		}
	}
}

// A dry run writes nothing, so there is nothing to bring the export in step
// with — and it must not pay for a full export either.
func TestApplyOpsLeavesTheExportAloneWhenNothingWasWritten(t *testing.T) {
	f := newWriteFixture(t)
	dir := exportFixtureDir(t, f)
	opsPath := filepath.Join(dir, OpsFileName)
	if err := os.WriteFile(opsPath, []byte(`
layer: `+testLayerID+`
ops:
  - at: "Employee #1@aaaa"
    set: {role_job_title: CFO}
`), 0o600); err != nil {
		t.Fatalf("write ops: %v", err)
	}

	f.reset()
	res, err := ApplyOpsFile(context.Background(), f.sim, opsPath, ApplyOptions{})
	if err != nil {
		t.Fatalf("ApplyOpsFile: %v", err)
	}
	if len(res.Applied) != 0 || res.Export != nil {
		t.Errorf("applied %d, export %v — an op already satisfied triggered a refresh",
			len(res.Applied), res.Export)
	}
	if f.layerReads != 0 || f.formReads != 0 {
		t.Errorf("read %d layer pages and %d forms, want neither", f.layerReads, f.formReads)
	}
}

// exportFixtureDir writes the two sidecars an ops file resolves against.
func exportFixtureDir(t *testing.T, f *writeFixture) string {
	t.Helper()
	dir := t.TempDir()
	writeFixtureIDs(t, dir)
	writeFixtureTypes(t, f, dir)
	return dir
}

func writeFixtureIDs(t *testing.T, dir string) {
	t.Helper()
	tree, _, err := BuildTree(File{LayerID: testLayerID, Actors: fixtureActors(t), Edges: fixtureEdgeList()})
	if err != nil {
		t.Fatalf("BuildTree: %v", err)
	}
	ids, err := RenderIDs(tree)
	if err != nil {
		t.Fatalf("RenderIDs: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, IDsFileName), ids, 0o600); err != nil {
		t.Fatalf("write ids: %v", err)
	}
}

func writeFixtureTypes(t *testing.T, f *writeFixture, dir string) {
	t.Helper()
	idx, err := LoadIndex(context.Background(), f.sim, testLayerID, "", 0)
	if err != nil {
		t.Fatalf("LoadIndex: %v", err)
	}
	schema, err := RenderTypesSchema(testLayerID, idx.Types)
	if err != nil {
		t.Fatalf("RenderTypesSchema: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, TypesFileName), schema, 0o600); err != nil {
		t.Fatalf("write types: %v", err)
	}
}

// A node's own description is its own key: 22 of the 50 types on a real layer
// carry a form field called "description", so a name in `set:` cannot mean
// both.
func TestApplyOpsWritesTheDescription(t *testing.T) {
	f := newWriteFixture(t)
	f.states[fixtureEmployeeA].description = "the CFO"

	res := f.apply(t, `
ops:
  - at: "Employee #1@aaaa"
    describe: |-
      Chief Financial Officer.
      Signs off on every contract over 10k.
`, ApplyOptions{})

	if len(res.Applied) != 1 {
		t.Fatalf("applied %d: %s", len(res.Applied), res.Plan.Render())
	}
	got, _ := f.writes[0].body["description"].(string)
	if !strings.Contains(got, "\n") {
		t.Errorf("description = %q, want the line break kept — the export collapses it, the write must not", got)
	}
	if _, sent := f.writes[0].body["data"]; sent {
		t.Errorf("a description-only op sent data: %v", f.writes[0].body)
	}
	if !strings.Contains(res.Plan.Render(), `description: "the CFO" -> "Chief Financial Officer.`) {
		t.Errorf("the plan does not show the description change:\n%s", res.Plan.Render())
	}
}

func TestPlanOpsSkipsADescriptionTheNodeAlreadyHas(t *testing.T) {
	f := newWriteFixture(t)
	f.states[fixtureEmployeeA].description = "the CFO"

	plan := f.plan(t, `
ops:
  - at: "Employee #1@aaaa"
    describe: the CFO
`)
	if len(plan.Actions) != 0 || len(plan.Satisfied) != 1 {
		t.Errorf("planned %d actions, %d satisfied", len(plan.Actions), len(plan.Satisfied))
	}
}

// A rename moves the node's address. The same ops file replayed afterwards
// addresses a name nothing answers to any more, so the name it renames to is
// tried as well — and finding it there means the work is done.
func TestApplyOpsRenameIsReplayable(t *testing.T) {
	f := newWriteFixture(t)

	const ops = `
ops:
  - at: "ACME > Docs"
    rename: Documents
`
	res := f.apply(t, ops, ApplyOptions{})
	if len(res.Applied) != 1 {
		t.Fatalf("first run applied %d: %s", len(res.Applied), res.Plan.Render())
	}

	// The layer as it is after the rename: the node answers to its new name.
	f.states[fixtureDocs].title = "Documents"
	f.renameFixtureNode(fixtureDocs, "Documents")

	res = f.apply(t, ops, ApplyOptions{})
	if len(res.Applied) != 0 || len(res.Plan.Satisfied) != 1 {
		t.Errorf("replay applied %d, satisfied %d: %s",
			len(res.Applied), len(res.Plan.Satisfied), res.Plan.Render())
	}
	if len(f.writes) != 1 {
		t.Errorf("two runs sent %d writes", len(f.writes))
	}
}

// An ambiguous address is never retried under the renamed name: a second
// guess at a node the writer has not chosen between is worse than the error.
func TestPlanOpsDoesNotRetryAnAmbiguousAddressUnderTheNewName(t *testing.T) {
	f := newWriteFixture(t)

	plan := f.plan(t, `
ops:
  - at: "Employee #1"
    rename: "Employee #1@aaaa"
`)
	if got := plan.errorFor(1); !strings.Contains(got, "ambiguous") {
		t.Errorf("error = %q, want the ambiguity kept", got)
	}
}

// A rename onto a sibling's title changes that sibling's address too — the
// next export has to tell the two apart.
func TestPlanOpsWarnsWhenARenameJoinsASibling(t *testing.T) {
	f := newWriteFixture(t)

	res := f.apply(t, `
ops:
  - at: "ACME > Docs"
    rename: "Employee #1"
`, ApplyOptions{DryRun: true})

	joined := strings.Join(res.Plan.Warnings, "\n")
	if !strings.Contains(joined, "joins a sibling") {
		t.Errorf("warnings = %q, want the collision reported", joined)
	}
	if len(res.Plan.Actions) != 1 {
		t.Errorf("the warning stopped the rename: %s", res.Plan.Render())
	}
}

// A stamped op is addressed by uuid, and the path beside it is not read at
// all. This is what makes a file replayable across its own rename: the first
// run renames the node and records its id, and the second finds the node the
// old `at:` no longer names.
func TestApplyOpsReplaysByIDAfterARename(t *testing.T) {
	f := newWriteFixture(t)

	dir := t.TempDir()
	opsPath := filepath.Join(dir, OpsFileName)
	const ops = `layer: ` + testLayerID + `
ops:
  - at: "ACME > Docs"
    rename: Documents
`
	if err := os.WriteFile(opsPath, []byte(ops), 0o644); err != nil {
		t.Fatalf("write ops: %v", err)
	}

	res, err := ApplyOpsFile(context.Background(), f.sim, opsPath, ApplyOptions{})
	if err != nil {
		t.Fatalf("first run: %v", err)
	}
	if len(res.Applied) != 1 || res.Stamped != 1 {
		t.Fatalf("first run applied %d and stamped %d, want 1 and 1", len(res.Applied), res.Stamped)
	}

	raw, err := os.ReadFile(opsPath)
	if err != nil {
		t.Fatalf("read the stamped file: %v", err)
	}
	if !strings.Contains(string(raw), "id: "+fixtureDocs) {
		t.Fatalf("the file was not stamped with the node id:\n%s", raw)
	}

	// The layer as it is after the rename: nothing answers to "Docs" now.
	f.states[fixtureDocs].title = "Documents"
	f.renameFixtureNode(fixtureDocs, "Documents")

	res, err = ApplyOpsFile(context.Background(), f.sim, opsPath, ApplyOptions{})
	if err != nil {
		t.Fatalf("replay: %v", err)
	}
	if len(res.Applied) != 0 || len(res.Plan.Satisfied) != 1 {
		t.Errorf("replay applied %d, satisfied %d: %s",
			len(res.Applied), len(res.Plan.Satisfied), res.Plan.Render())
	}
	if res.Stamped != 0 {
		t.Errorf("replay stamped %d ops, want none — the id was already there", res.Stamped)
	}
	if len(f.writes) != 1 {
		t.Errorf("two runs sent %d writes", len(f.writes))
	}
}

// The id wins over the path outright: an `at:` that names a different node is
// not consulted, because the whole point of the stamp is that the path may
// have gone stale.
func TestPlanOpsPrefersTheIDOverTheAddress(t *testing.T) {
	f := newWriteFixture(t)

	plan := f.plan(t, `
ops:
  - at: "ACME > Employee #1"
    id: `+fixtureDocs+`
    describe: addressed by id
`)
	if len(plan.Actions) != 1 {
		t.Fatalf("planned %d actions: %s", len(plan.Actions), plan.Render())
	}
	if got := plan.Actions[0].ActorID; got != fixtureDocs {
		t.Errorf("wrote to %s, want the id's node %s", got, fixtureDocs)
	}
}

// An id that is not on the layer is an error, not a fallback to the path: a
// node that was deleted must not have its facts written into whatever the
// stale address happens to reach now.
func TestPlanOpsFailsOnAnIDThatIsNotOnTheLayer(t *testing.T) {
	f := newWriteFixture(t)

	plan := f.plan(t, `
ops:
  - at: "ACME > Docs"
    id: 99999999-9999-4999-8999-999999999999
    describe: gone
`)
	got := plan.errorFor(1)
	if !strings.Contains(got, "not on this layer") {
		t.Errorf("error = %q, want it to say the id is not on the layer", got)
	}
	if len(plan.Actions) != 0 {
		t.Error("the op was planned against the path anyway")
	}
}

// A dry run writes nothing at all, and the ops file is part of "nothing".
func TestApplyOpsDryRunDoesNotStamp(t *testing.T) {
	f := newWriteFixture(t)

	dir := t.TempDir()
	opsPath := filepath.Join(dir, OpsFileName)
	const ops = `layer: ` + testLayerID + `
ops:
  - at: "ACME > Docs"
    rename: Documents
`
	if err := os.WriteFile(opsPath, []byte(ops), 0o644); err != nil {
		t.Fatalf("write ops: %v", err)
	}

	res, err := ApplyOpsFile(context.Background(), f.sim, opsPath, ApplyOptions{DryRun: true})
	if err != nil {
		t.Fatalf("dry run: %v", err)
	}
	if res.Stamped != 0 {
		t.Errorf("a dry run stamped %d ops", res.Stamped)
	}
	raw, err := os.ReadFile(opsPath)
	if err != nil {
		t.Fatalf("read back: %v", err)
	}
	if string(raw) != ops {
		t.Errorf("the dry run rewrote the ops file:\n%s", raw)
	}
}
