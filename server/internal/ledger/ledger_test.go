package ledger

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"migration-factory-plugin-mcp/internal/simulator"
)

// fake is the gateway as it actually answers: a pair route that creates or
// returns, an accounts route that echoes BOTH sides of a pair in no fixed
// order, and a transaction route that refuses a ref it has already seen with
// the 400 the platform really sends.
type fake struct {
	mu          sync.Mutex
	pairs       int
	attaches    int
	posted      map[string]float64 // accountId|ref -> amount
	shares      []shareCall        // one per pair-share call, in order
	shareStatus int                // non-zero makes every share fail with it
	dated       []int64            // originalDate of each accepted transaction, in order
	seenRef     map[string]bool
	srv         *httptest.Server
}

// shareCall is one POST /access_rules/account/{nameId}_{currencyId}.
type shareCall struct {
	objID     string
	action    string
	groupID   int
	privs     map[string]bool
	recursive string
}

func newFake(t *testing.T) *fake {
	t.Helper()
	f := &fake{posted: map[string]float64{}, seenRef: map[string]bool{}}
	mux := http.NewServeMux()
	mux.HandleFunc("/papi/1.0/accounts/pair/", func(w http.ResponseWriter, r *http.Request) {
		var body struct{ AccountName, CurrencyName string }
		_ = json.NewDecoder(r.Body).Decode(&body)
		f.mu.Lock()
		f.pairs++
		f.mu.Unlock()
		id := map[string]int{"UAH": 7, "USD": 9, "XXX": 11}[body.CurrencyName]
		_ = json.NewEncoder(w).Encode(map[string]any{"data": map[string]any{
			"accountName": map[string]any{"id": "name-" + body.AccountName},
			"currency":    map[string]any{"id": id},
		}})
	})
	mux.HandleFunc("/papi/1.0/accounts/", func(w http.ResponseWriter, r *http.Request) {
		var body simulator.CreateAccountRequest
		_ = json.NewDecoder(r.Body).Decode(&body)
		f.mu.Lock()
		f.attaches++
		f.mu.Unlock()
		cur := body.CurrencyID
		// credit first on purpose: the order is not stable in production.
		_ = json.NewEncoder(w).Encode(map[string]any{"data": []any{
			map[string]any{"id": "credit-acc", "nameId": body.NameID, "currencyId": cur, "incomeType": "credit"},
			map[string]any{"id": "debit-acc", "nameId": body.NameID, "currencyId": cur, "incomeType": "debit"},
		}})
	})
	mux.HandleFunc("/papi/1.0/access_rules/account/", func(w http.ResponseWriter, r *http.Request) {
		var body []struct {
			Action string `json:"action"`
			Data   struct {
				GroupID int             `json:"groupId"`
				Privs   map[string]bool `json:"privs"`
			} `json:"data"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		f.mu.Lock()
		defer f.mu.Unlock()
		if f.shareStatus != 0 {
			w.WriteHeader(f.shareStatus)
			_, _ = w.Write([]byte(`{"statusCode":403,"message":"Access Denied"}`))
			return
		}
		c := shareCall{
			objID:     strings.TrimPrefix(r.URL.Path, "/papi/1.0/access_rules/account/"),
			recursive: r.URL.Query().Get("recursive"),
		}
		if len(body) == 1 {
			c.action, c.groupID, c.privs = body[0].Action, body[0].Data.GroupID, body[0].Data.Privs
		}
		f.shares = append(f.shares, c)
		// The platform answers with the rules as they now stand plus the id of
		// the job that applies them — a grant is asynchronous.
		_ = json.NewEncoder(w).Encode(map[string]any{
			"data":   []any{map[string]any{"groupId": c.groupID, "privs": c.privs}},
			"taskId": 10848049,
		})
	})
	mux.HandleFunc("/papi/1.0/transactions/", func(w http.ResponseWriter, r *http.Request) {
		acc := strings.TrimPrefix(r.URL.Path, "/papi/1.0/transactions/")
		var body simulator.CreateTransactionRequest
		_ = json.NewDecoder(r.Body).Decode(&body)
		f.mu.Lock()
		defer f.mu.Unlock()
		if f.seenRef[body.Ref] {
			w.WriteHeader(http.StatusBadRequest)
			_, _ = w.Write([]byte(`{"statusCode":400,"error":"Bad Request","message":"Not unique ref"}`))
			return
		}
		f.seenRef[body.Ref] = true
		f.posted[acc+"|"+body.Ref] = body.Amount
		f.dated = append(f.dated, body.OriginalDate)
		_ = json.NewEncoder(w).Encode(map[string]any{"data": map[string]any{"id": 1}})
	})
	f.srv = httptest.NewServer(mux)
	t.Cleanup(f.srv.Close)
	return f
}

func (f *fake) client() *simulator.Client {
	return simulator.New(f.srv.URL+"/papi/1.0", simulator.WithAPIKey("k"))
}

func (f *fake) sideTotals() (debit, credit float64) {
	f.mu.Lock()
	defer f.mu.Unlock()
	for k, v := range f.posted {
		if strings.HasPrefix(k, "debit-acc|") {
			debit += v
		} else {
			credit += v
		}
	}
	return debit, credit
}

func write(t *testing.T, rows ...string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "s.jsonl")
	if err := os.WriteFile(p, []byte(strings.Join(rows, "\n")+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	return p
}

const actor = "b7b85b34-e8c0-415f-9d4f-66537a878b15"

func opts(path string) Options {
	return Options{AccountName: "Bank Statement", ActorID: actor, Path: path, WorkspaceID: "ws-1"}
}

// The central claim: each column lands on its own side, and a zero column is
// not a movement and is not posted.
func TestEachColumnLandsOnItsOwnSide(t *testing.T) {
	f := newFake(t)
	p := write(t,
		`{"transaction_date":"2026-09-21","debit_sum":"236.10","credit_sum":"0.00","currency":"UAH","description":"a"}`,
		`{"transaction_date":"2026-09-17","debit_sum":"0.00","credit_sum":"44.83","currency":"UAH","description":"b"}`)
	res, err := Post(context.Background(), f.client(), opts(p))
	if err != nil {
		t.Fatalf("Post: %v", err)
	}
	if res.Posted != 2 {
		t.Fatalf("posted %d, want 2 — a zero side must not be posted", res.Posted)
	}
	debit, credit := f.sideTotals()
	if debit != 236.10 || credit != 44.83 {
		t.Errorf("debit %.2f credit %.2f, want 236.10 / 44.83", debit, credit)
	}
}

// The pair route creates or returns, so asking it twice for one currency is
// waste, not error — the cache is what keeps a thousand-row file to one call.
func TestAPairIsResolvedOncePerCurrency(t *testing.T) {
	f := newFake(t)
	rows := make([]string, 0, 50)
	for i := 0; i < 50; i++ {
		rows = append(rows, `{"transaction_date":"2026-09-21","debit_sum":"1.00","credit_sum":"0.00","currency":"UAH","description":"x"}`)
	}
	if _, err := Post(context.Background(), f.client(), opts(write(t, rows...))); err != nil {
		t.Fatalf("Post: %v", err)
	}
	if f.pairs != 1 || f.attaches != 1 {
		t.Errorf("pairs=%d attaches=%d, want 1 and 1 for 50 rows of one currency", f.pairs, f.attaches)
	}
}

func TestEveryCurrencyGetsItsOwnPair(t *testing.T) {
	f := newFake(t)
	p := write(t,
		`{"transaction_date":"2026-09-21","debit_sum":"10.00","credit_sum":"0.00","currency":"UAH","description":"a"}`,
		`{"transaction_date":"2026-09-21","debit_sum":"20.00","credit_sum":"0.00","currency":"USD","description":"b"}`)
	res, err := Post(context.Background(), f.client(), opts(p))
	if err != nil {
		t.Fatalf("Post: %v", err)
	}
	if f.pairs != 2 {
		t.Errorf("pairs=%d, want 2", f.pairs)
	}
	if len(res.Currencies) != 2 || res.Currencies[0].Currency != "UAH" || res.Currencies[1].Currency != "USD" {
		t.Errorf("currencies %+v, want UAH and USD", res.Currencies)
	}
}

// Running the same file twice must not double the balance. This is the whole
// reason the ref is derived from the row.
func TestRerunningTheSameFilePostsNothingTwice(t *testing.T) {
	f := newFake(t)
	p := write(t,
		`{"transaction_date":"2026-09-21","debit_sum":"236.10","credit_sum":"0.00","currency":"UAH","description":"a"}`)
	if _, err := Post(context.Background(), f.client(), opts(p)); err != nil {
		t.Fatalf("first Post: %v", err)
	}
	res, err := Post(context.Background(), f.client(), opts(p))
	if err != nil {
		t.Fatalf("second Post: %v", err)
	}
	if res.Posted != 0 || res.Duplicate != 1 {
		t.Errorf("second run posted=%d duplicate=%d, want 0 and 1", res.Posted, res.Duplicate)
	}
	if res.Skipped != 0 {
		t.Errorf("a duplicate is not a failure, got skipped=%d", res.Skipped)
	}
	debit, _ := f.sideTotals()
	if debit != 236.10 {
		t.Errorf("debit %.2f after two runs, want 236.10", debit)
	}
}

// A row carrying both columns is two transactions; without the side in the ref
// the second would collide with the first and be swallowed as a duplicate.
func TestBothColumnsOnOneRowDoNotCollide(t *testing.T) {
	f := newFake(t)
	p := write(t,
		`{"transaction_date":"2026-09-21","debit_sum":"5.00","credit_sum":"7.00","currency":"UAH","description":"a"}`)
	res, err := Post(context.Background(), f.client(), opts(p))
	if err != nil {
		t.Fatalf("Post: %v", err)
	}
	if res.Posted != 2 || res.Duplicate != 0 {
		t.Fatalf("posted=%d duplicate=%d, want 2 and 0", res.Posted, res.Duplicate)
	}
	debit, credit := f.sideTotals()
	if debit != 5 || credit != 7 {
		t.Errorf("debit %.2f credit %.2f, want 5 and 7", debit, credit)
	}
}

// An unsigned contract that quietly accepts a sign would reverse a movement.
func TestASignedColumnIsRefused(t *testing.T) {
	f := newFake(t)
	p := write(t,
		`{"transaction_date":"2026-09-21","debit_sum":"-236.10","credit_sum":"0.00","currency":"UAH","description":"a"}`)
	_, err := Post(context.Background(), f.client(), opts(p))
	if err == nil || !strings.Contains(err.Error(), "unsigned") {
		t.Fatalf("err = %v, want a refusal naming the unsigned contract", err)
	}
}

func TestAMissingCurrencyBecomesXXX(t *testing.T) {
	f := newFake(t)
	p := write(t,
		`{"transaction_date":"2026-09-21","debit_sum":"1.00","credit_sum":"0.00","description":"a"}`)
	res, err := Post(context.Background(), f.client(), opts(p))
	if err != nil {
		t.Fatalf("Post: %v", err)
	}
	if len(res.Currencies) != 1 || res.Currencies[0].Currency != "XXX" {
		t.Errorf("currencies %+v, want one XXX", res.Currencies)
	}
}

func TestDryRunWritesNothing(t *testing.T) {
	f := newFake(t)
	p := write(t,
		`{"transaction_date":"2026-09-21","debit_sum":"236.10","credit_sum":"0.00","currency":"UAH","description":"a"}`)
	o := opts(p)
	o.DryRun = true
	res, err := Post(context.Background(), f.client(), o)
	if err != nil {
		t.Fatalf("Post: %v", err)
	}
	if res.Posted != 1 {
		t.Errorf("posted=%d, want 1 counted", res.Posted)
	}
	if d, c := f.sideTotals(); d != 0 || c != 0 {
		t.Errorf("dry run wrote %.2f/%.2f, want nothing", d, c)
	}
	if f.pairs != 1 {
		t.Errorf("a dry run still resolves the pair, pairs=%d", f.pairs)
	}
}

// The bug this field exists for: without originalDate the platform stamps every
// row with the moment of the import, so a statement loaded today reads as if it
// all happened today. The unit is milliseconds — the gateway's own UI divides
// by 1000 on the way out, and seconds here would land the row in 1970.
func TestARowIsDatedByItself(t *testing.T) {
	f := newFake(t)
	p := write(t,
		`{"transaction_date":"2026-09-21","debit_sum":"1.00","credit_sum":"0.00","currency":"UAH","description":"a"}`)
	if _, err := Post(context.Background(), f.client(), opts(p)); err != nil {
		t.Fatalf("Post: %v", err)
	}
	want := time.Date(2026, 9, 21, 0, 0, 0, 0, time.UTC).UnixMilli()
	if len(f.dated) != 1 || f.dated[0] != want {
		t.Fatalf("originalDate %v, want [%d] (%s)", f.dated, want, time.UnixMilli(want).UTC())
	}
}

// A statement that prints a time means it: the row happened then, not at
// midnight, and the two are a working day apart on a card.
func TestAPrintedTimeIsPartOfTheDate(t *testing.T) {
	f := newFake(t)
	p := write(t,
		`{"transaction_date":"2026-09-21","transaction_time":"08:20:02","debit_sum":"1.00","credit_sum":"0.00","currency":"UAH","description":"a"}`,
		`{"transaction_date":"2026-09-21","transaction_time":"09:30","debit_sum":"2.00","credit_sum":"0.00","currency":"UAH","description":"b"}`)
	if _, err := Post(context.Background(), f.client(), opts(p)); err != nil {
		t.Fatalf("Post: %v", err)
	}
	want := []int64{
		time.Date(2026, 9, 21, 8, 20, 2, 0, time.UTC).UnixMilli(),
		time.Date(2026, 9, 21, 9, 30, 0, 0, time.UTC).UnixMilli(),
	}
	if len(f.dated) != 2 || f.dated[0] != want[0] || f.dated[1] != want[1] {
		t.Fatalf("originalDate %v, want %v — hh:mm:ss and hh:mm are both what parsers print", f.dated, want)
	}
}

// The rows carry no offset, so the zone is a decision and not a detail: read in
// Kyiv, a row printed at 08:20 is three hours earlier in absolute time than the
// same row read as UTC.
func TestTheZoneTheClockIsReadInIsTheCallersToPick(t *testing.T) {
	f := newFake(t)
	p := write(t,
		`{"transaction_date":"2026-09-21","transaction_time":"08:20:02","debit_sum":"1.00","credit_sum":"0.00","currency":"UAH","description":"a"}`)
	o := opts(p)
	o.Timezone = "Europe/Kyiv"
	if _, err := Post(context.Background(), f.client(), o); err != nil {
		t.Fatalf("Post: %v", err)
	}
	kyiv, err := time.LoadLocation("Europe/Kyiv")
	if err != nil {
		t.Fatalf("Europe/Kyiv must resolve — the zone database is linked in: %v", err)
	}
	want := time.Date(2026, 9, 21, 8, 20, 2, 0, kyiv).UnixMilli()
	if len(f.dated) != 1 || f.dated[0] != want {
		t.Fatalf("originalDate %v, want [%d]", f.dated, want)
	}
}

// Silently falling back to UTC would be the same off-by-hours the field exists
// to stop, only harder to notice because the caller asked for a zone.
func TestAnUnknownZoneIsRefusedBeforeAnythingIsWritten(t *testing.T) {
	f := newFake(t)
	p := write(t,
		`{"transaction_date":"2026-09-21","debit_sum":"1.00","credit_sum":"0.00","currency":"UAH","description":"a"}`)
	o := opts(p)
	o.Timezone = "Europe/Atlantis"
	_, err := Post(context.Background(), f.client(), o)
	if err == nil || !strings.Contains(err.Error(), "unknown timezone") {
		t.Fatalf("err = %v, want a refusal naming the zone", err)
	}
	if d, c := f.sideTotals(); d != 0 || c != 0 {
		t.Errorf("the run wrote %.2f/%.2f before refusing", d, c)
	}
}

// A row with no usable date would be posted stamped with today, and afterwards
// nothing tells it apart from a row that really happened today.
func TestARowThatCannotSayWhenItHappenedStopsTheRun(t *testing.T) {
	for _, tc := range []struct{ name, row string }{
		{"missing", `{"debit_sum":"1.00","credit_sum":"0.00","currency":"UAH","description":"a"}`},
		{"not the contract's shape", `{"transaction_date":"21/09/2026","debit_sum":"1.00","credit_sum":"0.00","currency":"UAH","description":"a"}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := newFake(t)
			_, err := Post(context.Background(), f.client(), opts(write(t, tc.row)))
			if err == nil || !strings.Contains(err.Error(), "transaction_date") {
				t.Fatalf("err = %v, want a refusal naming transaction_date", err)
			}
			if d, c := f.sideTotals(); d != 0 || c != 0 {
				t.Errorf("the run wrote %.2f/%.2f before refusing", d, c)
			}
		})
	}
}

// A pair is workspace-level and a workspace outlives the run: the same person's
// next session asks for "Bank Statement" too, and the pair route answers the
// key that created it with 403 for everyone else. Naming the pair after the
// group in the name is what keeps that second run from failing on its first
// currency.
func TestThePairIsNamedAfterTheGroup(t *testing.T) {
	f := newFake(t)
	o := opts(write(t,
		`{"transaction_date":"2026-09-21","debit_sum":"10.00","credit_sum":"0.00","currency":"UAH","description":"a"}`))
	o.GroupID = 131107
	res, err := Post(context.Background(), f.client(), o)
	if err != nil {
		t.Fatal(err)
	}
	// The fake names the pair after what the request asked for.
	if len(res.Currencies) != 1 || res.Currencies[0].NameID != "name-Bank Statement 131107" {
		t.Errorf("the pair was bootstrapped under %+v, want the group in the name", res.Currencies)
	}
}

// Without a group there is nothing to scope by, and a suffix invented here
// would rename the pair of every run mf-api did not start.
func TestWithoutAGroupTheNameIsUsedAsPassed(t *testing.T) {
	f := newFake(t)
	res, err := Post(context.Background(), f.client(), opts(write(t,
		`{"transaction_date":"2026-09-21","debit_sum":"10.00","credit_sum":"0.00","currency":"UAH","description":"a"}`)))
	if err != nil {
		t.Fatal(err)
	}
	if res.Currencies[0].NameID != "name-Bank Statement" {
		t.Errorf("the pair was bootstrapped under %q, want the caller's own", res.Currencies[0].NameID)
	}
}

// The pair share is the difference between numbers that landed and numbers
// anybody can see: the bootstrap seeds access for the calling key alone, so
// without this the people the statement was imported for get an empty card.
func TestEachPairIsSharedWithItsGroupOncePerCurrency(t *testing.T) {
	f := newFake(t)
	o := opts(write(t,
		`{"transaction_date":"2026-09-21","debit_sum":"10.00","credit_sum":"0.00","currency":"UAH","description":"a"}`,
		`{"transaction_date":"2026-09-21","debit_sum":"0.00","credit_sum":"5.00","currency":"UAH","description":"b"}`,
		`{"transaction_date":"2026-09-21","debit_sum":"2.00","credit_sum":"0.00","currency":"USD","description":"c"}`))
	o.GroupID = 131107
	if _, err := Post(context.Background(), f.client(), o); err != nil {
		t.Fatal(err)
	}
	if len(f.shares) != 2 {
		t.Fatalf("want one share per currency, got %d: %+v", len(f.shares), f.shares)
	}
	seen := map[string]shareCall{}
	for _, c := range f.shares {
		seen[c.objID] = c
	}
	// The pair is addressed by its own id; there is no route taking the two apart.
	for _, want := range []string{"name-Bank Statement 131107_7", "name-Bank Statement 131107_9"} {
		c, ok := seen[want]
		if !ok {
			t.Fatalf("pair %q was not shared; shared: %v", want, seen)
		}
		if c.groupID != 131107 || c.action != "create" {
			t.Errorf("%s: want create for group 131107, got %q for %d", want, c.action, c.groupID)
		}
		// remove is withheld deliberately: the group reads the card, it does
		// not get to delete the account the statement was posted to.
		if !c.privs["view"] || !c.privs["modify"] || c.privs["remove"] {
			t.Errorf("%s: want view+modify and not remove, got %v", want, c.privs)
		}
		if c.recursive != "false" {
			t.Errorf("%s: want recursive=false explicitly, got %q", want, c.recursive)
		}
	}
}

func TestWithoutAGroupNothingIsShared(t *testing.T) {
	f := newFake(t)
	if _, err := Post(context.Background(), f.client(), opts(write(t,
		`{"transaction_date":"2026-09-21","debit_sum":"10.00","credit_sum":"0.00","currency":"UAH","description":"a"}`))); err != nil {
		t.Fatal(err)
	}
	if len(f.shares) != 0 {
		t.Fatalf("a run given no group granted something: %+v", f.shares)
	}
}

// A dry run resolves pairs like a real one, and resolving is the only thing it
// may leave behind — a grant is a write.
func TestADryRunGrantsNothing(t *testing.T) {
	f := newFake(t)
	o := opts(write(t,
		`{"transaction_date":"2026-09-21","debit_sum":"10.00","credit_sum":"0.00","currency":"UAH","description":"a"}`))
	o.GroupID, o.DryRun = 131107, true
	if _, err := Post(context.Background(), f.client(), o); err != nil {
		t.Fatal(err)
	}
	if len(f.shares) != 0 {
		t.Fatalf("dry run granted access: %+v", f.shares)
	}
}

// The key already has its own access, so a refused share costs no transaction.
// Abandoning a half-posted statement over a permission is the worse trade —
// but saying nothing about it is how a card silently shows no accounts.
func TestARefusedShareIsReportedAndTheRowsStillLand(t *testing.T) {
	f := newFake(t)
	f.shareStatus = http.StatusForbidden
	o := opts(write(t,
		`{"transaction_date":"2026-09-21","debit_sum":"10.00","credit_sum":"0.00","currency":"UAH","description":"a"}`))
	o.GroupID = 131107
	res, err := Post(context.Background(), f.client(), o)
	if err != nil {
		t.Fatalf("a refused share stopped the run: %v", err)
	}
	if res.Posted != 1 {
		t.Errorf("want the row posted anyway, posted %d", res.Posted)
	}
	if debit, _ := f.sideTotals(); debit != 10 {
		t.Errorf("want 10 on the debit side, got %v", debit)
	}
	if len(res.Warnings) != 1 || !strings.Contains(res.Warnings[0], "not shared with group 131107") {
		t.Fatalf("want the refusal reported, got %v", res.Warnings)
	}
}
