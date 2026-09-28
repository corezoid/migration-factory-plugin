package graph

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func statement(ref string, n int) StatementRecord {
	return StatementRecord{
		Ref:          ref,
		File:         "bank_statement_transactions.jsonl",
		ActorID:      "90bc5b30-7fae-4fa1-b294-ee7413e8e95d",
		Account:      "Bank Transaction",
		Transactions: n,
		Turnover: []StatementTurnover{
			{Currency: "UAH", Debit: 823750.52, Credit: 820479.47},
		},
	}
}

// A statement posted twice is one statement. Posting is idempotent by ref, so
// the second run writes nothing new and the tally must read the same — this is
// the whole reason an entry is replaced rather than appended.
func TestPostingTheSameStatementTwiceCountsItOnce(t *testing.T) {
	res := &RunResult{}

	if isNew := res.AddStatement(statement("stmt|actor|Bank Transaction|f.jsonl", 336)); !isNew {
		t.Fatal("the first posting should be new")
	}
	if isNew := res.AddStatement(statement("stmt|actor|Bank Transaction|f.jsonl", 336)); isNew {
		t.Fatal("the second posting of the same statement should not be new")
	}
	if got, want := res.TransactionsPosted, 336; got != want {
		t.Fatalf("transactions posted = %d, want %d", got, want)
	}
	if got, want := len(res.Statements), 1; got != want {
		t.Fatalf("statements = %d, want %d", got, want)
	}
}

// A second statement on the same actor is a second statement, and the count is
// the sum.
func TestASecondStatementAddsToTheCount(t *testing.T) {
	res := &RunResult{}
	res.AddStatement(statement("stmt|actor|Bank Transaction|july.jsonl", 336))
	res.AddStatement(statement("stmt|actor|Bank Transaction|august.jsonl", 41))

	if got, want := res.TransactionsPosted, 377; got != want {
		t.Fatalf("transactions posted = %d, want %d", got, want)
	}
}

// The transactions live beside the actor counts, not instead of them: a run
// that filled a hole and posted a statement did both, and the tally is the
// only machine-readable account of it.
func TestTheTallyCarriesBothHalvesOfTheRun(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ResultFileName)

	res := &RunResult{}
	res.Add([]*Action{{ActorID: "90bc5b30", FillsHole: true}})
	res.AddStatement(statement("stmt|90bc5b30|Bank Transaction|f.jsonl", 336))
	if err := WriteResult(path, res); err != nil {
		t.Fatal(err)
	}

	back, err := LoadResult(path)
	if err != nil {
		t.Fatal(err)
	}
	if back.HolesFilled != 1 || back.TransactionsPosted != 336 {
		t.Fatalf("read back %d hole(s) and %d transaction(s), want 1 and 336",
			back.HolesFilled, back.TransactionsPosted)
	}
}

// A tally written before this change has no statements key. It must read as
// zero transactions, not as a broken file — result.json is cumulative across
// runs and older ones are still on disk.
func TestATallyWithoutStatementsStillReads(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ResultFileName)
	old := `{
    "количество заполненных дырок": 1,
    "заполненные дырки": ["90bc5b30"]
}`
	if err := writeFile(path, []byte(old)); err != nil {
		t.Fatal(err)
	}

	res, err := LoadResult(path)
	if err != nil {
		t.Fatal(err)
	}
	if res.HolesFilled != 1 || res.TransactionsPosted != 0 {
		t.Fatalf("read %d hole(s), %d transaction(s); want 1 and 0",
			res.HolesFilled, res.TransactionsPosted)
	}

	// And it marshals with the new keys present rather than null, so a reader
	// that looks for them finds an empty list and not a missing field.
	if err := WriteResult(path, res); err != nil {
		t.Fatal(err)
	}
	var probe map[string]json.RawMessage
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, &probe); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"количество проведенных транзакций", "проведенные выписки"} {
		if _, ok := probe[key]; !ok {
			t.Fatalf("%q missing from a rewritten tally", key)
		}
	}
}

// StatementRef is what makes a re-post the same posting: only a different
// prefix, actor, account or file makes it a different one.
func TestTheStatementRefIgnoresTheDirectoryButNotThePrefix(t *testing.T) {
	a := StatementRef("", "actor", "Bank Transaction", "/run-1/f.jsonl")
	b := StatementRef("stmt", "actor", "Bank Transaction", "/run-2/f.jsonl")
	if a != b {
		t.Fatalf("the same statement from two run directories got two refs: %q and %q", a, b)
	}
	if c := StatementRef("again", "actor", "Bank Transaction", "/run-1/f.jsonl"); c == a {
		t.Fatal("a deliberate second posting under another prefix must be another entry")
	}
}
