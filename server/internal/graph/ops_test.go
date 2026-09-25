package graph

import (
	"strings"
	"testing"
)

func TestParseOpsReadsTheDocument(t *testing.T) {
	ops, err := ParseOps([]byte(`
layer: 96a5c7eb-2b1c-4c67-90be-5e754303d4f3
source_doc: brd-v3.md
ops:
  - at: "HRS > Finance > Employee #1"
    set:
      first_name: Ivan
      hired_at: 2024-03-01
  - at: "SMART CONTRACTS > Clinets"
    rename: Clients
unrouted:
  - text: Warehouse in Lviv opened Q3 2025
    guess: "ORG STRUCTURE > PHYSICAL INFRASTRUCTURE"
    reason: no BRANCH node for Lviv
`))
	if err != nil {
		t.Fatalf("ParseOps: %v", err)
	}

	if ops.Mode != ModeUpsert {
		t.Errorf("mode = %q, want the default %q", ops.Mode, ModeUpsert)
	}
	if len(ops.Ops) != 2 || len(ops.Unrouted) != 1 {
		t.Fatalf("parsed %d ops and %d unrouted", len(ops.Ops), len(ops.Unrouted))
	}
	if got := ops.Ops[0].Set["first_name"]; got != "Ivan" {
		t.Errorf("first_name = %v", got)
	}
	// An unquoted date is a timestamp to the YAML decoder and a string when
	// quoted; both have to reach the layer as the same day.
	if _, ok := ops.Ops[0].Set["hired_at"]; !ok {
		t.Error("hired_at missing from the op")
	}
	if ops.Ops[1].Rename != "Clients" {
		t.Errorf("rename = %q", ops.Ops[1].Rename)
	}
}

// The second way an op addresses its subject: a record of a type, by business
// key, instead of a node on the layer by path.
func TestParseOpsReadsARecordOp(t *testing.T) {
	ops, err := ParseOps([]byte(`
ops:
  - type: hrs_employee
    ref: "emp-koval-olena"
    rename: Olena Koval
    set:
      full_name: Olena Koval
`))
	if err != nil {
		t.Fatalf("ParseOps: %v", err)
	}
	if len(ops.Ops) != 1 {
		t.Fatalf("parsed %d ops", len(ops.Ops))
	}
	if ops.Ops[0].Type != "hrs_employee" || ops.Ops[0].Ref != "emp-koval-olena" {
		t.Errorf("type/ref = %q/%q", ops.Ops[0].Type, ops.Ops[0].Ref)
	}
	if ops.Ops[0].At != "" {
		t.Errorf("at = %q, want a record op to carry no path", ops.Ops[0].At)
	}
}

// A misspelt key is the one mistake a writer cannot see in the diff: the op
// simply is not in it. So it fails the parse instead.
func TestParseOpsRejectsAnUnknownKey(t *testing.T) {
	_, err := ParseOps([]byte(`
ops:
  - at: "ACME > Docs"
    sett:
      name: x
`))
	if err == nil || !strings.Contains(err.Error(), "sett") {
		t.Fatalf("err = %v, want the unknown key named", err)
	}
}

func TestParseOpsRejectsAnUnknownMode(t *testing.T) {
	if _, err := ParseOps([]byte("mode: replace\nops: []\n")); err == nil {
		t.Fatal("a mode that is neither upsert nor strict was accepted")
	}
}

// `source:` on an op is gone, and an unknown key is a parse error — so a file
// written for the older format fails by name rather than being applied with
// the anchor quietly dropped.
func TestParseOpsRejectsTheRetiredSourceKey(t *testing.T) {
	_, err := ParseOps([]byte(`
ops:
  - at: "ACME > Docs"
    rename: Documents
    source: brd-v3.md#L12
`))
	if err == nil || !strings.Contains(err.Error(), "source") {
		t.Fatalf("err = %v, want the retired key named", err)
	}
}

// The tree in graph.values.yaml prints "COMPANY [company]", and copying that
// line whole into an `at:` is the address that cannot match. The error says
// which part to drop rather than leaving the writer to reverse-engineer
// graph.ids.json, which is what the last run spent four turns doing.
func TestResolveNamesTheTypeSuffixAsTheProblem(t *testing.T) {
	paths := []string{"CORPORATION / HOLDING > COMPANY", "ERP > Suppliers > Supplier #1"}

	for _, tc := range []struct{ addr, want string }{
		{"COMPANY [company]", `address it as "COMPANY"`},
		{"Supplier #1 [suppliers]", `address it as "Supplier #1"`},
	} {
		_, err := matchPath(paths, tc.addr)
		if err == nil {
			t.Fatalf("%q resolved", tc.addr)
		}
		if !strings.Contains(err.Error(), tc.want) {
			t.Errorf("error = %v, want it to say %q", err, tc.want)
		}
	}

	// A node whose title genuinely ends in brackets is not a type suffix.
	if _, err := matchPath([]string{"Docs > Report [draft]"}, "Report [draft]"); err != nil {
		t.Errorf("a real title in brackets no longer resolves: %v", err)
	}
}
