package firecrawl

import (
	"encoding/json"
	"fmt"
)

// Error is a failed call to the instance: the HTTP status when there was a
// response (0 for a transport fault), a message, and the underlying error so
// errors.Is still sees a context deadline through it.
//
// There are no error classes here. The one reader of these messages is a
// model deciding what to do about the page it could not read, so what the
// message has to carry is not a code to switch on but the difference between
// "this page will never be readable", "try it again" and "the key is wrong" —
// which is what parseError writes into it.
type Error struct {
	Status  int
	Message string
	Err     error
}

func (e *Error) Error() string {
	if e.Status != 0 {
		return fmt.Sprintf("firecrawl: http %d: %s", e.Status, e.Message)
	}
	return "firecrawl: " + e.Message
}

func (e *Error) Unwrap() error { return e.Err }

// parseError maps a non-2xx response to an error that says what the status
// means for the caller's next move.
func parseError(status int, body []byte) *Error {
	var hint string
	switch {
	case status == 429:
		hint = "the provider is rate limiting us — read the page again in a moment"
	case status == 403:
		hint = "the site refused the provider; this address cannot be read at all, so do not retry it"
	case status == 404:
		hint = "there is no page at that address — check the link you followed it from"
	case status == 400:
		hint = "the address was rejected as malformed"
	case status == 401 || status == 402:
		hint = "the Firecrawl key was rejected or is out of credit — configuration, not the page"
	case status >= 500:
		hint = "the Firecrawl instance itself failed — reading the page again may well work"
	default:
		hint = "the provider refused the request"
	}
	return &Error{Status: status, Message: hint + ": " + providerMessage(body)}
}

// providerMessage pulls the human message out of Firecrawl's error body,
// which is {"success":false,"error":"…"}, falling back to the raw body.
func providerMessage(body []byte) string {
	var env struct {
		Error   string `json:"error"`
		Message string `json:"message"`
	}
	if json.Unmarshal(body, &env) == nil {
		if env.Error != "" {
			return env.Error
		}
		if env.Message != "" {
			return env.Message
		}
	}
	if s := string(body); s != "" {
		return truncate(s, 300)
	}
	return "no body"
}

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n] + "…"
	}
	return s
}
