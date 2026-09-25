package graph

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"migration-factory-plugin-mcp/internal/simulator"
)

// The fixture form: a counterparty type marking one identity key the way the
// twin forms do, plus an iban that is not marked and a second type marking
// none at all.
const fixtureRecordTypes = `types:
  suppliers:
    formId: 801
    form: ACME_SUPPLIER
    fields:
      tax_id: {id: tax_id, type: string, title: "Tax number — STRONG identity key — digits as written"}
      supplier_name: {id: supplier_name, type: string, title: "Legal or trading name of the supplier"}
      iban: {id: item_5001, type: string, title: "Supplier bank account for payments — IBAN, no spaces"}
      source: {id: source, type: string, title: "Source of these values"}
  notes:
    formId: 802
    form: ACME_NOTE
    fields:
      note_text: {id: note_text, type: text, title: "The note"}
`

// recordsFixture serves a form and its actor register, matching the way the
// gateway does: the field query is exact and case-sensitive, the title search
// is a case-insensitive substring.
type recordsFixture struct {
	sim *simulator.Client
	mu  sync.Mutex
	// probes are the queries the fixture answered, in order.
	probes []url.Values
	// actors is the register: field id -> value, plus id/ref/title.
	actors  []map[string]any
	formErr bool
	// queryErr answers any field query with a 500, the shape of a probe that
	// cannot be run.
	queryErr bool
}

func newRecordsFixture(t *testing.T, actors []map[string]any) *recordsFixture {
	t.Helper()

	f := &recordsFixture{actors: actors}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := strings.TrimPrefix(r.URL.Path, "/papi/1.0")
		switch {
		case strings.HasPrefix(path, "/forms/"):
			if f.formErr {
				w.WriteHeader(http.StatusForbidden)
				_, _ = io.WriteString(w, `{"message":"Access Denied"}`)
				return
			}
			id := strings.TrimPrefix(path, "/forms/")
			_, _ = io.WriteString(w, `{"data":{"id":`+id+`,"accId":"ws-7","title":"ACME_SUPPLIER"}}`)

		case strings.HasPrefix(path, "/actors_filters/"):
			q := r.URL.Query()
			f.mu.Lock()
			f.probes = append(f.probes, q)
			f.mu.Unlock()

			if f.queryErr && q.Get("q") != "" {
				w.WriteHeader(http.StatusInternalServerError)
				_, _ = io.WriteString(w, `{"message":"boom"}`)
				return
			}

			var hits []map[string]any
			for _, a := range f.actors {
				if matchesProbe(a, q) {
					hits = append(hits, a)
				}
			}
			body, _ := json.Marshal(map[string]any{"data": map[string]any{"list": hits}})
			_, _ = w.Write(body)

		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(srv.Close)

	f.sim = simulator.New(srv.URL, simulator.WithAPIKey("k3y"))
	return f
}

// matchesProbe is the gateway's own matching, as observed: `q=<field>=<value>`
// is exact and case-sensitive against the stored value, `search` is a
// case-insensitive substring of the title.
func matchesProbe(actor map[string]any, q url.Values) bool {
	if expr := q.Get("q"); expr != "" {
		field, want, ok := strings.Cut(expr, "=")
		if !ok {
			return false
		}
		data, _ := actor["data"].(map[string]any)
		got, present := data[field]
		return present && fmt.Sprint(got) == want
	}
	if s := q.Get("search"); s != "" {
		title, _ := actor["title"].(string)
		return strings.Contains(strings.ToLower(title), strings.ToLower(s))
	}
	return false
}

func (f *recordsFixture) queries() []url.Values {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.probes
}

// exprs renders the probes as "<field>=<value>" / "title~<value>" so a test
// can assert the order they ran in.
func (f *recordsFixture) exprs() []string {
	var out []string
	for _, q := range f.queries() {
		switch {
		case q.Get("q") != "":
			out = append(out, q.Get("q"))
		case q.Get("search") != "":
			out = append(out, "title~"+q.Get("search"))
		}
	}
	return out
}

func actor(id, ref, title string, data map[string]any) map[string]any {
	return map[string]any{"id": id, "ref": ref, "title": title, "formId": 801, "data": data}
}

// recordsDir writes a types schema into a fresh directory, the way an export
// leaves one behind.
func recordsDir(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, TypesFileName), []byte(fixtureRecordTypes), 0o600); err != nil {
		t.Fatalf("write types schema: %v", err)
	}
	return dir
}

func find(t *testing.T, f *recordsFixture, opts FindRecordsOptions) *FindResult {
	t.Helper()
	if opts.Dir == "" {
		opts.Dir = recordsDir(t)
	}
	if opts.Type == "" {
		opts.Type = "suppliers"
	}
	opts.Concurrency = 1
	res, err := FindRecords(context.Background(), f.sim, opts)
	if err != nil {
		t.Fatalf("FindRecords: %v", err)
	}
	return res
}

func checkFor(t *testing.T, res *FindResult, value string) ValueCheck {
	t.Helper()
	for _, c := range res.Checks {
		if c.Value == value {
			return c
		}
	}
	t.Fatalf("no check for %q in %+v", value, res.Checks)
	return ValueCheck{}
}

func TestFindRecordsMatchesTheIdentityKey(t *testing.T) {
	f := newRecordsFixture(t, []map[string]any{
		actor("00000001-1111-4111-8111-111111111111", "supplier-dante", "DANTE INTERNATIONAL SA",
			map[string]any{"tax_id": "14399840", "item_5001": "RO73INGB0001008199078940"}),
	})

	res := find(t, f, FindRecordsOptions{Values: []string{"14399840", "6738347"}})

	hit := checkFor(t, res, "14399840")
	if !hit.Found() || hit.MatchedBy != "tax_id" {
		t.Fatalf("check = %+v, want a tax_id hit", hit)
	}
	if hit.Records[0].Ref != "supplier-dante" || hit.Records[0].Title != "DANTE INTERNATIONAL SA" {
		t.Errorf("record = %+v", hit.Records[0])
	}
	// The identity values come back so the writer can see it is the right
	// record rather than a namesake — and only those: a field nobody marked
	// and nobody probed is not part of the answer.
	if hit.Records[0].Fields["tax_id"] != "14399840" {
		t.Errorf("fields = %v", hit.Records[0].Fields)
	}
	if _, present := hit.Records[0].Fields["iban"]; present {
		t.Errorf("fields = %v, want no column for a field neither marked nor probed", hit.Records[0].Fields)
	}

	if miss := checkFor(t, res, "6738347"); miss.Found() || miss.Err != nil {
		t.Errorf("check = %+v, want a clean not-found", miss)
	}
	if found, missing, failed := res.Tally(); found != 1 || missing != 1 || failed != 0 {
		t.Errorf("tally = %d/%d/%d", found, missing, failed)
	}
}

// Probes run in the order given and the first hit ends the search, so a later
// probe cannot overrule an earlier one. Naming a field the type does not mark
// is how a source that identifies subjects by account number gets checked at
// all — nothing in the server knows what an "iban" is.
func TestFindRecordsProbesInOrderAndStopsAtTheFirstHit(t *testing.T) {
	f := newRecordsFixture(t, []map[string]any{
		actor("00000002-1111-4111-8111-111111111111", "supplier-iulius", "IULIUS MALL TIMISOARA SRL",
			map[string]any{"item_5001": "RO84RNCB0175055419400001"}),
	})

	res := find(t, f, FindRecordsOptions{
		Fields: []string{"tax_id", "iban", "title"},
		Values: []string{"RO84RNCB0175055419400001"},
	})

	hit := checkFor(t, res, "RO84RNCB0175055419400001")
	if hit.MatchedBy != "iban" {
		t.Fatalf("matched by %q, want the iban", hit.MatchedBy)
	}
	want := []string{"tax_id=RO84RNCB0175055419400001", "item_5001=RO84RNCB0175055419400001"}
	if got := f.exprs(); strings.Join(got, "|") != strings.Join(want, "|") {
		t.Errorf("probes = %v, want %v — identity key first, and no title probe after a hit", got, want)
	}
	if strings.Join(res.Probes, ",") != "tax_id,iban,title" {
		t.Errorf("probes reported = %v", res.Probes)
	}
	if len(res.Warnings) != 1 || !strings.Contains(res.Warnings[0], `"iban" is not marked`) {
		t.Errorf("warnings = %v, want the unmarked field reported", res.Warnings)
	}
}

// The gateway's field query is exact and case-sensitive, and a statement
// prints IBANs with spaces in them.
func TestFindRecordsNormalisesTheValueForTheQuery(t *testing.T) {
	f := newRecordsFixture(t, []map[string]any{
		actor("00000003-1111-4111-8111-111111111111", "supplier-elanul", "ELANUL GALBEN SRL",
			map[string]any{"item_5001": "RO64INGB0000999905388885"}),
	})

	res := find(t, f, FindRecordsOptions{
		Fields: []string{"tax_id", "iban"},
		Values: []string{"RO64 INGB 0000 9999 0538 8885"},
	})

	if hit := res.Checks[0]; !hit.Found() || hit.MatchedBy != "iban" {
		t.Fatalf("check = %+v, want the spaced IBAN to find the stored one", hit)
	}
	want := []string{
		"tax_id=RO64 INGB 0000 9999 0538 8885", "tax_id=RO64INGB0000999905388885",
		"item_5001=RO64 INGB 0000 9999 0538 8885", "item_5001=RO64INGB0000999905388885",
	}
	if got := f.exprs(); strings.Join(got, "|") != strings.Join(want, "|") {
		t.Errorf("probes = %v, want %v — each field tried as written, then normalised", got, want)
	}
}

// The case the register is full of: a record created from a bank statement
// carries an account and no registration number, so the identity-key probe
// misses it. The title probe is what stops the duplicate.
func TestFindRecordsFallsBackToTheTitle(t *testing.T) {
	f := newRecordsFixture(t, []map[string]any{
		actor("00000004-1111-4111-8111-111111111111", "iban-RO43RNCB0175148248250001", "IULIUS MALL CLUJ SRL",
			map[string]any{"item_5001": "RO43RNCB0175148248250001"}),
	})

	res := find(t, f, FindRecordsOptions{Values: []string{"iulius mall cluj srl"}})

	hit := res.Checks[0]
	if !hit.Found() || hit.MatchedBy != probeTitle {
		t.Fatalf("check = %+v, want the title probe to catch it", hit)
	}
	if hit.Records[0].Ref != "iban-RO43RNCB0175148248250001" {
		t.Errorf("record = %+v, want the ref to write with", hit.Records[0])
	}
}

// The title search is the gateway's substring match, and a substring is not
// an identity: "MALL" inside "IULIUS MALL CLUJ SRL" is a lead the writer sees,
// not a record the writer writes to.
func TestFindRecordsReportsATitleSubstringAsSimilarNotFound(t *testing.T) {
	f := newRecordsFixture(t, []map[string]any{
		actor("00000004-1111-4111-8111-111111111111", "iban-RO43RNCB0175148248250001", "IULIUS MALL CLUJ SRL",
			map[string]any{"item_5001": "RO43RNCB0175148248250001"}),
	})

	res := find(t, f, FindRecordsOptions{Values: []string{"MALL", "Iulius Mall Cluj SRL"}})

	near := res.Checks[0]
	if near.Found() || near.MatchedBy != "" {
		t.Fatalf("check = %+v, want a substring hit reported as not found", near)
	}
	if len(near.Similar) != 1 || near.Similar[0].Title != "IULIUS MALL CLUJ SRL" {
		t.Errorf("similar = %+v, want the near-miss listed", near.Similar)
	}
	if exact := res.Checks[1]; !exact.Found() || exact.MatchedBy != probeTitle || len(exact.Similar) != 0 {
		t.Errorf("check = %+v, want the whole title to match, case aside", exact)
	}
	if found, missing, _ := res.Tally(); found != 1 || missing != 1 {
		t.Errorf("tally = %d found, %d missing; a similar title must not count as found", found, missing)
	}
}

// A record made by hand has no ref, and no `ref:` lookup reaches it — the
// answer has to carry the id, or the next run duplicates it.
func TestFindRecordsReportsARecordWithNoRef(t *testing.T) {
	f := newRecordsFixture(t, []map[string]any{
		actor("00000005-1111-4111-8111-111111111111", "", "GINZA INVEST SRL",
			map[string]any{"tax_id": "44112233"}),
	})

	res := find(t, f, FindRecordsOptions{Values: []string{"44112233"}})

	rec := res.Checks[0].Records[0]
	if rec.Ref != "" || rec.ID != "00000005-1111-4111-8111-111111111111" {
		t.Fatalf("record = %+v", rec)
	}
	if out := renderCheckLine(res); !strings.Contains(out, "NO REF") {
		t.Errorf("rendered = %q, want the missing ref called out", out)
	}
}

// Two records answering one value is the register's own ambiguity, and it is
// the writer's to resolve — both come back rather than the first.
func TestFindRecordsReportsEveryMatch(t *testing.T) {
	f := newRecordsFixture(t, []map[string]any{
		actor("00000006-1111-4111-8111-111111111111", "invictus-medical-37185315", "INVICTUS MEDICAL HEALTH SRL",
			map[string]any{"tax_id": "37185315"}),
		actor("00000007-1111-4111-8111-111111111111", "client-invictus-37185315", "INVICTUS MEDICAL HEALTH SRL",
			map[string]any{"tax_id": "37185315"}),
	})

	res := find(t, f, FindRecordsOptions{Values: []string{"37185315"}})
	if got := len(res.Checks[0].Records); got != 2 {
		t.Fatalf("records = %d, want both duplicates reported", got)
	}
}

// A named field is the caller saying which key is unique, so nothing else is
// consulted — in particular not the title, which would answer about a record
// the named field says nothing about.
func TestFindRecordsWithANamedFieldProbesOnlyThat(t *testing.T) {
	f := newRecordsFixture(t, []map[string]any{
		actor("00000008-1111-4111-8111-111111111111", "supplier-pastex", "PASTEX COM SRL",
			map[string]any{"tax_id": "2896218"}),
	})

	res := find(t, f, FindRecordsOptions{Fields: []string{"tax_id"}, Values: []string{"PASTEX COM SRL"}})

	if res.Checks[0].Found() {
		t.Errorf("check = %+v, want no title match when a field was named", res.Checks[0])
	}
	if got := f.exprs(); len(got) != 1 || got[0] != "tax_id=PASTEX COM SRL" {
		t.Errorf("probes = %v, want the named field alone", got)
	}
	if strings.Join(res.Probes, ",") != "tax_id" {
		t.Errorf("probes reported = %v", res.Probes)
	}
}

// Naming a field that does not identify anything is allowed — iban is exactly
// that and it is the useful probe — but the answer says so.
func TestFindRecordsWarnsOnANonIdentityField(t *testing.T) {
	f := newRecordsFixture(t, nil)

	res := find(t, f, FindRecordsOptions{Fields: []string{"supplier_name"}, Values: []string{"X"}})
	if len(res.Warnings) != 1 || !strings.Contains(res.Warnings[0], "identity key") {
		t.Errorf("warnings = %v", res.Warnings)
	}
}

// A type marking nothing still gets checked, on the iban and the title, and
// the caller is told the check is weaker than it looks.
func TestFindRecordsWarnsWhenTheTypeMarksNoIdentityKey(t *testing.T) {
	f := newRecordsFixture(t, nil)

	res := find(t, f, FindRecordsOptions{Type: "notes", Values: []string{"X"}})
	if len(res.Warnings) != 1 || !strings.Contains(res.Warnings[0], "no field as an identity key") {
		t.Fatalf("warnings = %v", res.Warnings)
	}
	if strings.Join(res.Probes, ",") != probeTitle {
		t.Errorf("probes = %v, want the title alone", res.Probes)
	}
}

// A probe that failed is not a record that is absent. Reporting it as
// not-found is what would license the create.
func TestFindRecordsReportsAFailedProbeAsUnknown(t *testing.T) {
	f := newRecordsFixture(t, nil)
	f.queryErr = true

	res := find(t, f, FindRecordsOptions{Values: []string{"14399840"}})
	if res.Checks[0].Err == nil || res.Checks[0].Found() {
		t.Fatalf("check = %+v, want an error rather than a clean miss", res.Checks[0])
	}
	if _, _, failed := res.Tally(); failed != 1 {
		t.Errorf("tally reports no failure")
	}
}

func TestFindRecordsRejectsAnEmptyValueList(t *testing.T) {
	f := newRecordsFixture(t, nil)

	_, err := FindRecords(context.Background(), f.sim, FindRecordsOptions{
		Dir: recordsDir(t), Type: "suppliers", Values: []string{"", "   "},
	})
	if err == nil || !strings.Contains(err.Error(), "nothing to check") {
		t.Errorf("error = %v", err)
	}
}

func TestFindRecordsRejectsAnUnknownFieldAndType(t *testing.T) {
	f := newRecordsFixture(t, nil)
	dir := recordsDir(t)

	_, err := FindRecords(context.Background(), f.sim, FindRecordsOptions{
		Dir: dir, Type: "suppliers", Fields: []string{"vat_number"}, Values: []string{"x"},
	})
	if err == nil || !strings.Contains(err.Error(), "supplier_name") {
		t.Errorf("field error = %v, want the type's real fields", err)
	}

	_, err = FindRecords(context.Background(), f.sim, FindRecordsOptions{
		Dir: dir, Type: "crm_clients", Values: []string{"x"},
	})
	if err == nil || !strings.Contains(err.Error(), "suppliers") {
		t.Errorf("type error = %v, want the slugs this export has", err)
	}
}

func TestFindRecordsWithoutAnExport(t *testing.T) {
	f := newRecordsFixture(t, nil)

	_, err := FindRecords(context.Background(), f.sim, FindRecordsOptions{
		Dir: t.TempDir(), Type: "suppliers", Values: []string{"x"},
	})
	if err == nil || !strings.Contains(err.Error(), TypesFileName) {
		t.Errorf("error = %v, want the missing export named", err)
	}
}

// The workspace is read off the form, so a form that cannot be read costs the
// accId and nothing else: the probes still go out, with a warning.
func TestFindRecordsWarnsWhenTheWorkspaceCannotBeRead(t *testing.T) {
	f := newRecordsFixture(t, nil)
	f.formErr = true

	res := find(t, f, FindRecordsOptions{Values: []string{"14399840"}})
	if len(res.Warnings) != 1 || !strings.Contains(res.Warnings[0], "workspace") {
		t.Fatalf("warnings = %v", res.Warnings)
	}
	if q := f.queries()[0]; q.Get("accId") != "" {
		t.Errorf("accId = %q, want none", q.Get("accId"))
	}
}

// renderCheckLine is the rendering the tool layer does, reached from here so
// the "no ref" case is asserted where it is produced.
func renderCheckLine(res *FindResult) string {
	var b strings.Builder
	for _, c := range res.Checks {
		for _, r := range c.Records {
			if r.Ref == "" {
				b.WriteString("NO REF " + r.ID)
			} else {
				b.WriteString(r.Ref)
			}
		}
	}
	return b.String()
}

// A field the caller named is a column of the answer even though the type
// does not mark it: the hit has to show the value it was found by.
func TestFindRecordsShowsTheProbedFieldInTheAnswer(t *testing.T) {
	f := newRecordsFixture(t, []map[string]any{
		actor("00000009-1111-4111-8111-111111111111", "supplier-dacoda", "DACODA SRL",
			map[string]any{"tax_id": "77001122", "item_5001": "RO22BTRLRONCRT0T2038DF01"}),
	})

	res := find(t, f, FindRecordsOptions{
		Fields: []string{"iban"},
		Values: []string{"RO22BTRLRONCRT0T2038DF01"},
	})

	got := res.Checks[0].Records[0].Fields
	if got["iban"] != "RO22BTRLRONCRT0T2038DF01" {
		t.Errorf("fields = %v, want the probed field", got)
	}
	// The type's own identity key rides along: it is what the next document
	// will look this record up by.
	if got["tax_id"] != "77001122" {
		t.Errorf("fields = %v, want the type's identity key too", got)
	}
}

// "title" in `fields` asks for the title probe alongside named fields, in the
// position it was given.
func TestFindRecordsTitleCanBeNamedAmongTheFields(t *testing.T) {
	f := newRecordsFixture(t, []map[string]any{
		actor("0000000a-1111-4111-8111-111111111111", "supplier-ginza", "GINZA INVEST SRL", nil),
	})

	res := find(t, f, FindRecordsOptions{
		Fields: []string{"tax_id", "title"},
		Values: []string{"ginza invest srl"},
	})
	if !res.Checks[0].Found() || res.Checks[0].MatchedBy != probeTitle {
		t.Fatalf("check = %+v, want the named title probe to run", res.Checks[0])
	}
	if strings.Join(res.Probes, ",") != "tax_id,title" {
		t.Errorf("probes = %v", res.Probes)
	}
}

// `fields` naming nothing the type has is a mistake worth stopping on: the
// alternative is a check that probes nothing and answers "not found".
func TestFindRecordsRejectsFieldsThatProbeNothing(t *testing.T) {
	f := newRecordsFixture(t, nil)

	_, err := FindRecords(context.Background(), f.sim, FindRecordsOptions{
		Dir: recordsDir(t), Type: "suppliers", Fields: []string{"  "}, Values: []string{"x"},
	})
	if err == nil || !strings.Contains(err.Error(), "no field left to probe") {
		t.Errorf("error = %v", err)
	}
}
