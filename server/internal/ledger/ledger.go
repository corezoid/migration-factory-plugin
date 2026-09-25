// Package ledger posts a parsed bank statement onto an actor as transactions.
//
// The input is the JSONL a statement parser produces — one object per
// transaction, carrying a date, a currency, and the two turnover columns:
//
//	{"transaction_date":"2026-09-21","transaction_time":"08:20:02",
//	 "debit_sum":"236.10","credit_sum":"0.00","currency":"UAH",
//	 "description":"MAGAZiN 5499"}
//
// One account name plus one currency is an account pair, and a pair on an
// actor is two rows with their own ids — a debit one and a credit one. A
// transaction has no direction of its own: the side is the id it is posted to.
// So each row's debit_sum goes to the debit id and its credit_sum to the credit
// id, and the card's own credit-minus-debit total is then the net movement,
// with both turnovers still readable separately. Nothing is signed to achieve
// that, which is what keeps these numbers comparable with the ones the
// statement prints about itself.
//
// A file may hold more than one currency. Each gets its own pair, resolved on
// first sight and remembered for the rest of the run — `POST /accounts/pair`
// creates or returns, so the remembering is purely to avoid asking twice, and
// a cold cache is never a correctness problem.
//
// Each row is posted with its own date. The platform stamps a transaction with
// the moment of the call and keeps the real one in `originalDate`, so a run
// that does not send it produces a statement every row of which happened on
// import day — which is what the whole file exists to say otherwise.
package ledger

import (
	"bufio"
	"context"
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"
	// The zone database is linked in rather than read from the host: this runs
	// in whatever container the agent was given, a scratch image has no
	// /usr/share/zoneinfo, and a Timezone that silently failed to resolve is
	// the same off-by-three-hours bug as sending no date at all.
	_ "time/tzdata"

	"migration-factory-plugin-mcp/internal/simulator"
)

// maxLine caps a single JSONL line. A statement row is a few hundred bytes; a
// megabyte of it means the file is not what the caller thinks it is.
const maxLine = 1 << 20

// Record is one parsed transaction.
type Record struct {
	Date        string `json:"transaction_date"`
	Time        string `json:"transaction_time,omitempty"`
	Debit       string `json:"debit_sum"`
	Credit      string `json:"credit_sum"`
	Currency    string `json:"currency"`
	Description string `json:"description"`
}

// Options is one posting run.
type Options struct {
	AccountName string // the account-name category, e.g. "Bank Statement"
	ActorID     string // the actor the accounts hang on
	Path        string // the .jsonl to read
	WorkspaceID string // where the pair lives
	GroupID     int    // Single Account group each pair is named after and shared to; 0 does neither
	RefPrefix   string // namespaces the idempotency refs; defaults to "stmt"
	Timezone    string // IANA zone the rows' wall clock is read in; defaults to UTC
	DryRun      bool   // resolve and count, post nothing
}

// Result is what the run did.
type Result struct {
	Records    int
	Posted     int
	Duplicate  int
	Skipped    int
	Currencies []CurrencyTally
	Warnings   []string
}

// CurrencyTally is one currency's share of the run.
type CurrencyTally struct {
	Currency   string
	Rows       int
	Debit      float64
	Credit     float64
	DebitID    string
	CreditID   string
	NameID     string
	CurrencyID int
}

// sides is a resolved pair on the actor.
type sides struct {
	simulator.AccountSides
	nameID     string
	currencyID int
}

// Post reads the file and records every row. Resolution is lazy and cached per
// currency; a row whose posting fails is counted and reported rather than
// stopping the run, because a statement half-recorded and silent about it is
// worse than one that says which rows it lost.
func Post(ctx context.Context, sim *simulator.Client, opts Options) (*Result, error) {
	if strings.TrimSpace(opts.AccountName) == "" {
		return nil, fmt.Errorf("no account name: pass `account_id` with the account-name category to record under")
	}
	if strings.TrimSpace(opts.ActorID) == "" {
		return nil, fmt.Errorf("no actor: pass `actor_id` with the actor UUID the accounts belong to")
	}
	prefix := opts.RefPrefix
	if prefix == "" {
		prefix = "stmt"
	}
	loc, err := location(opts.Timezone)
	if err != nil {
		return nil, err
	}

	f, err := os.Open(opts.Path)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", opts.Path, err)
	}
	defer f.Close()

	res := &Result{}
	cache := map[string]sides{}
	tally := map[string]*CurrencyTally{}

	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), maxLine)
	for line := 0; sc.Scan(); {
		line++
		raw := strings.TrimSpace(sc.Text())
		if raw == "" {
			continue
		}
		var r Record
		if err := json.Unmarshal([]byte(raw), &r); err != nil {
			return nil, fmt.Errorf("%s line %d is not JSON: %w", opts.Path, line, err)
		}
		res.Records++

		cur := strings.ToUpper(strings.TrimSpace(r.Currency))
		if cur == "" {
			cur = "XXX"
		}
		debit, credit, err := amounts(r)
		if err != nil {
			return nil, fmt.Errorf("%s line %d: %w", opts.Path, line, err)
		}
		when, err := occurredAt(r, loc)
		if err != nil {
			return nil, fmt.Errorf("%s line %d: %w", opts.Path, line, err)
		}

		s, ok := cache[cur]
		if !ok {
			s, err = resolve(ctx, sim, opts, cur, res)
			if err != nil {
				return nil, fmt.Errorf("currency %s: %w", cur, err)
			}
			cache[cur] = s
		}
		t, ok := tally[cur]
		if !ok {
			t = &CurrencyTally{Currency: cur, DebitID: s.Debit, CreditID: s.Credit,
				NameID: s.nameID, CurrencyID: s.currencyID}
			tally[cur] = t
		}
		t.Rows++
		t.Debit += debit
		t.Credit += credit

		// Two sides, so a row carrying both is two transactions; a zero side
		// is not posted at all, because a zero movement is not a movement.
		for _, p := range []struct {
			amount  float64
			account string
			side    string
		}{
			{debit, s.Debit, simulator.IncomeTypeDebit},
			{credit, s.Credit, simulator.IncomeTypeCredit},
		} {
			if p.amount == 0 {
				continue
			}
			if opts.DryRun {
				res.Posted++
				continue
			}
			ref := refFor(prefix, raw, p.side)
			_, err := sim.CreateTransaction(ctx, p.account, simulator.CreateTransactionRequest{
				Amount:       p.amount,
				Comment:      comment(r),
				Ref:          ref,
				Data:         payload(r),
				OriginalDate: when,
			})
			switch {
			case err == nil:
				res.Posted++
			case simulator.IsDuplicateRef(err):
				// Posted by an earlier run. That is the ref doing its job.
				res.Duplicate++
			default:
				res.Skipped++
				res.Warnings = append(res.Warnings,
					fmt.Sprintf("line %d %s %s: %v", line, cur, p.side, err))
			}
		}
	}
	if err := sc.Err(); err != nil {
		return nil, fmt.Errorf("read %s: %w", opts.Path, err)
	}

	for _, t := range tally {
		res.Currencies = append(res.Currencies, *t)
	}
	sort.Slice(res.Currencies, func(i, j int) bool {
		return res.Currencies[i].Currency < res.Currencies[j].Currency
	})
	return res, nil
}

// resolve bootstraps the pair and attaches it to the actor, returning both
// sides. The pair call creates or returns, so it is made without asking first.
func resolve(ctx context.Context, sim *simulator.Client, opts Options, currency string, res *Result) (sides, error) {
	nameID, currencyID, err := sim.EnsureAccountPair(ctx, opts.WorkspaceID, pairName(opts.AccountName, opts.GroupID), currency)
	if err != nil {
		return sides{}, fmt.Errorf("ensure account pair (%s, %s): %w", opts.AccountName, currency, err)
	}
	sharePair(ctx, sim, opts, currency, nameID, currencyID, res)
	acc, err := sim.EnsureActorAccount(ctx, opts.ActorID, simulator.CreateAccountRequest{
		NameID: nameID, CurrencyID: currencyID, AccountType: "fact", Search: true,
	})
	if err != nil {
		return sides{}, fmt.Errorf("attach account to actor: %w", err)
	}
	return sides{AccountSides: acc, nameID: nameID, currencyID: currencyID}, nil
}

// pairName is the name the pair is bootstrapped under: the caller's with the
// session's group appended.
//
// A pair is workspace-level while a run is not, and the bootstrap grants the
// key that created it and nobody else — so the same person's next session asks
// for "Bank Statement" again and is refused 403, with nothing it can do about
// it: only a grantee or an Owner can widen the rule, and neither is here at
// import time. The group in the name gives each session's people their own
// pair instead. No group — a run mf-api did not start — no suffix.
func pairName(name string, groupID int) string {
	name = strings.TrimSpace(name)
	if groupID <= 0 {
		return name
	}
	return name + " " + strconv.Itoa(groupID)
}

// sharePair hands the session's group the pair the bootstrap just seeded for
// this run's key alone. Once per currency, because resolve is cached.
//
// Best-effort on purpose: the key already has its own access, so a refused share
// costs no transaction, and abandoning a half-posted statement over a permission
// is the worse trade. It is reported instead — a reported share is one somebody
// can repeat by hand, while a silent one leaves a card that shows no accounts and
// nothing anywhere saying why.
//
// A dry run grants nothing. It resolves pairs like a real run, and resolving is
// the only thing it is allowed to leave behind.
func sharePair(ctx context.Context, sim *simulator.Client, opts Options, currency, nameID string, currencyID int, res *Result) {
	if opts.GroupID <= 0 || opts.DryRun {
		return
	}
	if err := sim.ShareAccountPairWithGroup(ctx, nameID, currencyID, opts.GroupID); err != nil {
		log.Printf("share pair %s_%d (%s, %s) with group %d: %v",
			nameID, currencyID, opts.AccountName, currency, opts.GroupID, err)
		res.Warnings = append(res.Warnings, fmt.Sprintf(
			"pair (%s, %s) is not shared with group %d, so only this run's key can see what it posted: %v",
			opts.AccountName, currency, opts.GroupID, err))
	}
}

// amounts reads the two columns. They are strings on purpose in the record, so
// that nothing upstream re-floats them; here is where they finally become
// numbers, and a malformed one stops the run rather than posting a zero.
func amounts(r Record) (debit, credit float64, err error) {
	if debit, err = amount(r.Debit); err != nil {
		return 0, 0, fmt.Errorf("debit_sum: %w", err)
	}
	if credit, err = amount(r.Credit); err != nil {
		return 0, 0, fmt.Errorf("credit_sum: %w", err)
	}
	return debit, credit, nil
}

func amount(s string) (float64, error) {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0, nil
	}
	v, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return 0, fmt.Errorf("%q is not a number", s)
	}
	if v < 0 {
		// The record's two columns are unsigned by contract; a negative here
		// means the producer put a sign where a side was meant to carry the
		// meaning, and posting it would silently reverse the movement.
		return 0, fmt.Errorf("%q is negative — debit_sum and credit_sum are unsigned, the side carries the direction", s)
	}
	return v, nil
}

// refFor derives the idempotency reference from the row itself, so re-running
// the same file posts nothing twice and a run interrupted halfway can simply be
// run again. The side is part of it because a row with both columns filled is
// two transactions that must not collide.
func refFor(prefix, rawLine, side string) string {
	sum := sha1.Sum([]byte(rawLine))
	return prefix + "-" + hex.EncodeToString(sum[:])[:32] + "-" + side
}

// occurredAt turns the row's own date — and its time, where the statement
// printed one — into the epoch milliseconds `originalDate` takes. A row that
// cannot say when it happened stops the run instead of being posted: the
// platform's fallback is the moment of the call, so the alternative is a
// transaction confidently dated today, which reads as a fact rather than as a
// gap and is not distinguishable later from a row that really did.
//
// The layouts are the JSONL contract's and nothing else. A date shaped some
// other way is a parser that broke its contract, and guessing at it is how
// 03/04 becomes April in one file and March in the next.
func occurredAt(r Record, loc *time.Location) (int64, error) {
	date := strings.TrimSpace(r.Date)
	if date == "" {
		return 0, errors.New("no transaction_date: a row that cannot say when it happened would be stamped with today")
	}
	value, layouts := date, []string{"2006-01-02"}
	if clock := strings.TrimSpace(r.Time); clock != "" {
		// Seconds first: a statement that prints them is the common case, and
		// hh:mm is what is left when a parser found only minutes.
		value, layouts = date+" "+clock, []string{"2006-01-02 15:04:05", "2006-01-02 15:04"}
	}
	for _, l := range layouts {
		if t, err := time.ParseInLocation(l, value, loc); err == nil {
			return t.UnixMilli(), nil
		}
	}
	return 0, fmt.Errorf("transaction_date %q with transaction_time %q is not yyyy-mm-dd [hh:mm[:ss]]", r.Date, r.Time)
}

// location is the zone the rows' wall clock is read in. A statement prints no
// offset — a row says 08:20:02 and means it in whatever zone the bank keeps —
// so something has to decide, and the decision moves a row by hours and a
// midnight row across a day. UTC is the default because it invents nothing;
// a statement whose bank is known is better posted in that bank's zone, which
// is what Timezone is for. A name that does not resolve is refused rather than
// quietly falling back to UTC: a run that ignored the zone it was handed is
// the same silent off-by-hours this file exists to stop.
func location(name string) (*time.Location, error) {
	name = strings.TrimSpace(name)
	if name == "" {
		return time.UTC, nil
	}
	loc, err := time.LoadLocation(name)
	if err != nil {
		return nil, fmt.Errorf("unknown timezone %q: pass an IANA name such as Europe/Kyiv, or leave it out to read the statement's clock as UTC", name)
	}
	return loc, nil
}

// comment is what a person reads on the transaction. The date stays in it even
// though `originalDate` now carries it as a date: the comment is the one field
// every listing of a transaction shows, and a row whose date lives only in a
// column the view happens not to print reads as undated.
func comment(r Record) string {
	when := r.Date
	if r.Time != "" {
		when += " " + r.Time
	}
	d := strings.TrimSpace(r.Description)
	if d == "" {
		return when
	}
	return when + " " + d
}

// payload keeps the row's own fields on the transaction, so the statement's
// date remains machine-readable after import and not only inside a comment.
func payload(r Record) map[string]any {
	d := map[string]any{"transaction_date": r.Date}
	if r.Time != "" {
		d["transaction_time"] = r.Time
	}
	if r.Description != "" {
		d["description"] = r.Description
	}
	return d
}
