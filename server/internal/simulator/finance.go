package simulator

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
)

// The slice of the accounts API needed to record money on an actor: bootstrap
// the workspace (account-name, currency) pair so the caller has access to it,
// attach the account to the actor, and post transactions against it.
//
// The shape to know before reading further: one (name, currency) pair on an
// actor is TWO rows, each with its own account id — one `incomeType: "debit"`,
// one `"credit"`. A transaction carries no direction of its own; the side is
// decided entirely by which of the two ids it is posted to, and the card totals
// the pair as credit minus debit. So a statement's two turnover columns map
// onto the two sides exactly, and nothing has to be signed to make it work.

// CreateAccountRequest creates one account on an actor. NameID and CurrencyID
// are required.
type CreateAccountRequest struct {
	NameID      string `json:"nameId"`
	CurrencyID  int    `json:"currencyId"`
	AccountType string `json:"accountType,omitempty"`
	Search      bool   `json:"search,omitempty"`
}

// Account is one side of one account on an actor.
type Account struct {
	ID         string `json:"id"`
	NameID     string `json:"nameId"`
	CurrencyID int    `json:"currencyId"`
	IncomeType string `json:"incomeType"`
}

// The two sides a pair is held as.
const (
	IncomeTypeDebit  = "debit"
	IncomeTypeCredit = "credit"
)

// AccountSides is both ids of one (name, currency) pair on one actor.
type AccountSides struct {
	Debit  string
	Credit string
}

// EnsureActorAccount makes sure an actor carries the pair and returns both
// sides. The create is idempotent (ignoreIfExist) and answers with the actor's
// accounts, so the wanted rows are usually among them; the endpoint does not
// always echo an account that already existed, so that case reads it back.
// Safe to call on every visit.
//
// Both sides are returned rather than one, because which side a value belongs
// on is the caller's question and not this client's: a tally that only grows
// goes on credit, money leaving an account goes on debit, and picking here
// would decide that for everyone.
func (c *Client) EnsureActorAccount(ctx context.Context, actorID string, req CreateAccountRequest) (AccountSides, error) {
	if err := validateActorID(actorID); err != nil {
		return AccountSides{}, err
	}
	var created listEnvelope[Account]
	if err := c.call(ctx, request{
		method: http.MethodPost,
		path:   "/accounts/" + seg(actorID),
		query:  url.Values{"ignoreIfExist": {"true"}},
		body:   req,
	}, &created); err != nil {
		return AccountSides{}, err
	}
	if s := sidesOf(created.Data, req.NameID, req.CurrencyID); s.complete() {
		return s, nil
	}
	// The create did not echo the accounts (they already existed); read back.
	accts, err := c.GetActorAccounts(ctx, actorID)
	if err != nil {
		return AccountSides{}, err
	}
	if s := sidesOf(accts, req.NameID, req.CurrencyID); s.complete() {
		return s, nil
	}
	return AccountSides{}, fmt.Errorf("simulator: account %s/%d not present on actor %s after ensure",
		req.NameID, req.CurrencyID, actorID)
}

func (s AccountSides) complete() bool { return s.Debit != "" && s.Credit != "" }

// sidesOf picks both rows of a pair out of an actor's accounts. The gateway
// lists the two in no fixed order, so each is matched on its incomeType rather
// than on position — taking whichever came first is how half a ledger ends up
// on the wrong side, displayed negated.
func sidesOf(accts []Account, nameID string, currencyID int) AccountSides {
	var s AccountSides
	for i := range accts {
		a := &accts[i]
		if a.NameID != nameID || a.CurrencyID != currencyID {
			continue
		}
		switch a.IncomeType {
		case IncomeTypeDebit:
			s.Debit = a.ID
		case IncomeTypeCredit:
			s.Credit = a.ID
		}
	}
	return s
}

// GetActorAccounts reads an actor's accounts.
func (c *Client) GetActorAccounts(ctx context.Context, actorID string) ([]Account, error) {
	if err := validateActorID(actorID); err != nil {
		return nil, err
	}
	// The listing is paginated (default ~20) and an actor can carry many
	// accounts, so ask for the whole page — the pair we want must not fall off.
	var out listEnvelope[Account]
	if err := c.get(ctx, "/accounts/"+seg(actorID), url.Values{"limit": {"100"}}, &out); err != nil {
		return nil, err
	}
	return out.Data, nil
}

// accountPair is the body of POST /accounts/pair: the created (or found)
// account-name and currency, each carrying the id an account is built from.
type accountPair struct {
	AccountName struct {
		ID string `json:"id"`
	} `json:"accountName"`
	Currency struct {
		ID int `json:"id"`
	} `json:"currency"`
}

// EnsureAccountPair bootstraps the workspace (account-name, currency) pair and
// grants the caller access to it, returning the two ids an account is built
// from. This is the step that makes later transaction calls work: access is
// enforced on the pair, and attaching the account alone never seeds it — so
// without this bootstrap a non-owner key gets 403 on every transaction. The
// name and the currency are created if missing; safe to repeat.
//
// There is deliberately no "does it exist" check before this call: the route
// creates or returns, so asking first would be a second request that can only
// ever agree with the one that follows it. Any caching around this is a cost
// optimisation, never a correctness one.
//
// It also resolves the currency, which is why the currency arrives here as a
// display name and leaves as a numeric id: there is no separate currency
// lookup on this path, and resolution is by exact name, so "USD" and "usd"
// stay distinct.
func (c *Client) EnsureAccountPair(ctx context.Context, workspaceID, accountName, currencyName string) (nameID string, currencyID int, err error) {
	if workspaceID == "" {
		return "", 0, fmt.Errorf("simulator: no workspace for the account pair: set SIM_WORKSPACE_ID")
	}
	var out itemEnvelope[accountPair]
	if err := c.call(ctx, request{
		method: http.MethodPost,
		path:   "/accounts/pair/" + seg(workspaceID),
		body:   map[string]any{"accountName": accountName, "currencyName": currencyName},
	}, &out); err != nil {
		return "", 0, err
	}
	return out.Data.AccountName.ID, out.Data.Currency.ID, nil
}

// Transaction is the receipt of a recorded movement.
type Transaction struct {
	ID int64 `json:"id"`
}

// CreateTransactionRequest records a value on one side of an account. Amount is
// the real value in the account's currency, not scaled by its precision — the
// precision only rounds the display. A stable Ref makes the call idempotent.
// There is no direction field: the side is the account id this is posted to.
type CreateTransactionRequest struct {
	Amount  float64        `json:"amount"`
	Comment string         `json:"comment,omitempty"`
	Ref     string         `json:"ref,omitempty"`
	Data    map[string]any `json:"data,omitempty"`

	// OriginalDate is when the movement actually happened, in epoch
	// MILLISECONDS. Left out, the platform stamps the transaction with the
	// moment of the call, so an imported statement reads as if every row of it
	// happened on import day — the dates are not lost in the file, they are
	// simply never sent. Milliseconds and not seconds: the gateway's own UI
	// multiplies by 1000 on the way in and divides by 1000 on the way out, and
	// a seconds value here lands the row in 1970.
	OriginalDate int64 `json:"originalDate,omitempty"`
}

// CreateTransaction records a transaction on one account. The account's pair
// access must already be seeded (see EnsureAccountPair) or the call is denied.
func (c *Client) CreateTransaction(ctx context.Context, accountID string, req CreateTransactionRequest) (*Transaction, error) {
	var out itemEnvelope[Transaction]
	if err := c.call(ctx, request{
		method: http.MethodPost,
		path:   "/transactions/" + seg(accountID),
		body:   req,
	}, &out); err != nil {
		return nil, err
	}
	return &out.Data, nil
}
