package graph_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"migration-factory-plugin-mcp/internal/graph"
)

// writeOps puts an ops file in a temp directory and returns its path.
func writeOps(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), graph.OpsFileName)
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write ops: %v", err)
	}
	return path
}

func readFile(t *testing.T, path string) string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read back: %v", err)
	}
	return string(raw)
}

func TestStampOpsAddsTheIDUnderTheAddress(t *testing.T) {
	path := writeOps(t, `layer: L1

ops:
  - at: "COMPANY"
    set:
      city: Bucharest

  - at: "Lead #1"
    rename: ACME
`)

	stamped, warnings, err := graph.StampOps(path, map[int]string{1: "uuid-1", 2: "uuid-2"})
	if err != nil {
		t.Fatalf("StampOps: %v", err)
	}
	if stamped != 2 || len(warnings) != 0 {
		t.Fatalf("stamped %d with warnings %v, want 2 and none", stamped, warnings)
	}

	// The id goes directly under the address it was resolved from, at the
	// same indentation, so the pair reads as one thing.
	want := `layer: L1

ops:
  - at: "COMPANY"
    id: uuid-1
    set:
      city: Bucharest

  - at: "Lead #1"
    id: uuid-2
    rename: ACME
`
	if got := readFile(t, path); got != want {
		t.Errorf("stamped file:\n%s\nwant:\n%s", got, want)
	}
}

func TestStampOpsLeavesEverythingElseByteForByte(t *testing.T) {
	// A round trip through the YAML encoder would keep the comments and drop
	// the blank lines. The file is something a person reads next to the plan,
	// so nothing but the new lines may move.
	body := `# an import of a bank statement
layer: L1
source_doc: statement.pdf   # two spaces before this comment

ops:
  # the company itself
  - at: "COMPANY"
    describe: |-
      Romanian road freight carrier.
      Second line, indented block scalar.
    set:
      amount: 125000.00
      when: "2026-09-10"
      confidence: 0.92
`
	path := writeOps(t, body)
	if _, _, err := graph.StampOps(path, map[int]string{1: "uuid-1"}); err != nil {
		t.Fatalf("StampOps: %v", err)
	}

	got := readFile(t, path)
	if want := strings.Replace(body, "  - at: \"COMPANY\"\n", "  - at: \"COMPANY\"\n    id: uuid-1\n", 1); got != want {
		t.Errorf("stamped file:\n%s\nwant only the id line added:\n%s", got, want)
	}
}

func TestStampOpsSkipsOpsThatAlreadyCarryAnID(t *testing.T) {
	path := writeOps(t, `layer: L1
ops:
  - at: "COMPANY"
    id: uuid-1
    set:
      city: Bucharest
  - at: "Lead #1"
    set:
      source: doc
`)
	before := readFile(t, path)

	stamped, _, err := graph.StampOps(path, map[int]string{1: "uuid-1", 2: "uuid-2"})
	if err != nil {
		t.Fatalf("StampOps: %v", err)
	}
	if stamped != 1 {
		t.Errorf("stamped %d ops, want only the one that had no id", stamped)
	}
	got := readFile(t, path)
	if strings.Count(got, "id: uuid-1") != 1 {
		t.Errorf("the existing id was touched:\n%s", got)
	}
	if !strings.Contains(got, "id: uuid-2") {
		t.Errorf("the second op was not stamped:\n%s", got)
	}
	if len(got) <= len(before) {
		t.Error("nothing was added to the file")
	}
}

func TestStampOpsWarnsRatherThanCorruptAMultiLineAddress(t *testing.T) {
	// A folded address has no line whose end this can be sure of, and an id
	// inserted into the middle of one would silently change the address.
	path := writeOps(t, `layer: L1
ops:
  - at: >-
      COMPANY
    set:
      city: Bucharest
`)
	before := readFile(t, path)

	stamped, warnings, err := graph.StampOps(path, map[int]string{1: "uuid-1"})
	if err != nil {
		t.Fatalf("StampOps: %v", err)
	}
	if stamped != 0 {
		t.Errorf("stamped %d ops, want none", stamped)
	}
	if len(warnings) != 1 || !strings.Contains(warnings[0], "op #1") {
		t.Errorf("warnings = %v, want one naming op #1", warnings)
	}
	if got := readFile(t, path); got != before {
		t.Errorf("the file was rewritten anyway:\n%s", got)
	}
}

// A quoted or plain address that wraps onto a second line parses as one
// scalar, and the parser reports only where it starts. Stamping after its
// first line would put the id inside the address — the file would still parse,
// with `at: "ACME > id: uuid-1 Docs"` and an empty `id:`, and nothing would
// say so. Every wrapped shape is refused with a warning and the file left as it
// was.
func TestStampOpsWarnsRatherThanCorruptAWrappedAddress(t *testing.T) {
	for name, body := range map[string]string{
		"double-quoted": `layer: L1
ops:
  - at: "ACME >
      Docs"
    set:
      city: Bucharest
`,
		"single-quoted": `layer: L1
ops:
  - at: 'ACME >
      Docs'
    set:
      city: Bucharest
`,
		"plain": `layer: L1
ops:
  - at: ACME >
      Docs
    set:
      city: Bucharest
`,
		"plain across a blank line": `layer: L1
ops:
  - at: ACME

      Docs
    set:
      city: Bucharest
`,
	} {
		t.Run(name, func(t *testing.T) {
			path := writeOps(t, body)

			stamped, warnings, err := graph.StampOps(path, map[int]string{1: "uuid-1"})
			if err != nil {
				t.Fatalf("StampOps: %v", err)
			}
			if stamped != 0 {
				t.Errorf("stamped %d ops, want none", stamped)
			}
			if len(warnings) != 1 || !strings.Contains(warnings[0], "op #1") {
				t.Errorf("warnings = %v, want one naming op #1", warnings)
			}
			if got := readFile(t, path); got != body {
				t.Errorf("the file was rewritten anyway:\n%s", got)
			}
		})
	}
}

// The wrapped-address check reads the raw line, and the raw line may carry
// more than the address: a comment, a `#` inside the quotes, an escaped quote.
// None of those is a reason to refuse.
func TestStampOpsStampsAOneLineAddressWhateverFollowsIt(t *testing.T) {
	path := writeOps(t, `layer: L1
ops:
  - at: "COMPANY"   # the root
    set: {city: Bucharest}
  - at: "Lead #1"
    set: {city: Bucharest}
  - at: 'O''Neil > Docs'
    set: {city: Bucharest}
  - at: "Says \"hi\""
    set: {city: Bucharest}
  - at: ACME > Docs   # plain, then a comment
    set: {city: Bucharest}
  - at: ACME > Docs
    # a comment line, indented like a continuation but not one
    set: {city: Bucharest}
`)

	stamped, warnings, err := graph.StampOps(path, map[int]string{
		1: "uuid-1", 2: "uuid-2", 3: "uuid-3", 4: "uuid-4", 5: "uuid-5", 6: "uuid-6",
	})
	if err != nil {
		t.Fatalf("StampOps: %v", err)
	}
	if stamped != 6 || len(warnings) != 0 {
		t.Fatalf("stamped %d with warnings %v, want all six and none", stamped, warnings)
	}
	got := readFile(t, path)
	for _, want := range []string{
		"  - at: \"COMPANY\"   # the root\n    id: uuid-1\n",
		"  - at: \"Lead #1\"\n    id: uuid-2\n",
		"  - at: 'O''Neil > Docs'\n    id: uuid-3\n",
		"  - at: \"Says \\\"hi\\\"\"\n    id: uuid-4\n",
		"  - at: ACME > Docs   # plain, then a comment\n    id: uuid-5\n",
		"  - at: ACME > Docs\n    id: uuid-6\n    # a comment line",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("stamped file lacks %q:\n%s", want, got)
		}
	}
}

func TestStampOpsOnlyStampsWhatResolved(t *testing.T) {
	// An op that matched no node has nothing to record, and must not shift
	// the lines of the ops around it.
	path := writeOps(t, `layer: L1
ops:
  - at: "COMPANY"
    set:
      city: Bucharest
  - at: "nowhere"
    set:
      city: Bucharest
  - at: "Lead #1"
    set:
      source: doc
`)

	stamped, _, err := graph.StampOps(path, map[int]string{1: "uuid-1", 3: "uuid-3"})
	if err != nil {
		t.Fatalf("StampOps: %v", err)
	}
	if stamped != 2 {
		t.Errorf("stamped %d ops, want 2", stamped)
	}

	got := readFile(t, path)
	want := `layer: L1
ops:
  - at: "COMPANY"
    id: uuid-1
    set:
      city: Bucharest
  - at: "nowhere"
    set:
      city: Bucharest
  - at: "Lead #1"
    id: uuid-3
    set:
      source: doc
`
	if got != want {
		t.Errorf("stamped file:\n%s\nwant:\n%s", got, want)
	}
}

func TestStampOpsIsANoOpWithoutResolutions(t *testing.T) {
	path := writeOps(t, "layer: L1\nops: []\n")
	before := readFile(t, path)

	stamped, warnings, err := graph.StampOps(path, nil)
	if err != nil || stamped != 0 || warnings != nil {
		t.Fatalf("StampOps(nil) = %d, %v, %v — want a silent no-op", stamped, warnings, err)
	}
	if got := readFile(t, path); got != before {
		t.Error("the file was rewritten")
	}
}

func TestStampOpsRefusesAFileWithoutOps(t *testing.T) {
	path := writeOps(t, "layer: L1\nnotops: []\n")
	if _, _, err := graph.StampOps(path, map[int]string{1: "uuid-1"}); err == nil {
		t.Fatal("StampOps accepted a file with no `ops:` list")
	}
}
