package graph

import (
	"strings"
	"testing"
)

func TestCleanValuesNormalisesMultiformKeys(t *testing.T) {
	got := cleanValues(map[string]any{
		"full_name":         "Ivan",
		"__form__701:role":  "CFO",
		"__form__701:empty": "",
		"blank":             "   ",
		"nothing":           nil,
		"empty_list":        []any{},
		"zero":              0,
	})

	if got["role"] != "CFO" {
		t.Errorf("the leaf form prefix was not stripped: %v", got)
	}
	if got["full_name"] != "Ivan" {
		t.Errorf("values = %v", got)
	}
	if got["zero"] != 0 {
		t.Error("zero is a value, not an empty field")
	}
	for _, key := range []string{"empty", "blank", "nothing", "empty_list"} {
		if _, ok := got[key]; ok {
			t.Errorf("empty field %q survived: %v", key, got)
		}
	}
}

func TestCleanValuesReturnsNilForAnEmptyActor(t *testing.T) {
	if got := cleanValues(map[string]any{"a": nil, "b": ""}); got != nil {
		t.Errorf("cleanValues = %v, want nil", got)
	}
}

func TestFormatValueRendersTheShapesTheAPIReturns(t *testing.T) {
	cases := []struct {
		name string
		in   any
		want string
	}{
		{"string", "Ivan Petrov", "Ivan Petrov"},
		{"multiline", "two\nlines", "two lines"},
		{"number", 0.9, "0.9"},
		{"bool", true, "true"},
		{"select", []any{map[string]any{"title": "Queued", "value": "QUEUED"}}, "Queued"},
		{"reference", map[string]any{"id": "x", "name": "Acme"}, "Acme"},
		{"nil", nil, ""},
	}
	for _, c := range cases {
		if got := formatValue(c.in); got != c.want {
			t.Errorf("%s: formatValue = %q, want %q", c.name, got, c.want)
		}
	}
}

func TestFormatValueKeepsTheWholeValue(t *testing.T) {
	long := strings.Repeat("a", 400)
	if got := formatValue(long); got != long {
		t.Errorf("formatValue cut a value: %d runes of %d", len([]rune(got)), len(long))
	}
}
