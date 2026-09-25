package firecrawl

import "strings"

// The `images` format answers with addresses and nothing else, and an address
// on its own identifies nobody: what ties a photograph to a person, a product
// or an office is the text beside it. So a read that asks for pictures asks
// for the page's cleaned HTML too, and this is where the two are joined — the
// alt text and the title of every <img>, against the address it sits on.
//
// The HTML is Firecrawl's cleaned rendering: script, style and head are gone,
// relative addresses are already absolute and a responsive srcset is already
// resolved to its largest version. That is what makes a scanner this small
// enough — there is no document to understand here, only <img> tags to read.

// imageAlts reads the alt text and title of every <img> in the page, keyed by
// the address it is on.
func imageAlts(html string) map[string]string {
	out := map[string]string{}
	for _, tag := range imgTags(html) {
		attrs := attributes(tag)
		src := firstAttr(attrs, "src", "data-src", "data-original")
		if src == "" {
			continue
		}
		// The first mention wins: a page that repeats an image usually says
		// the most about it the first time, and a later empty alt on the same
		// address must not erase what the first one carried.
		if _, seen := out[src]; seen {
			continue
		}
		if text := firstAttr(attrs, "alt", "title", "aria-label"); text != "" {
			out[src] = text
		} else {
			out[src] = ""
		}
	}
	return out
}

// imgTags returns the body of every <img …> tag, quotes respected so a ">"
// inside an alt text does not end the tag early.
func imgTags(html string) []string {
	var tags []string
	rest := html
	for {
		i := indexTag(rest, "<img")
		if i < 0 {
			return tags
		}
		rest = rest[i+len("<img"):]

		var (
			quote byte
			end   = -1
		)
		for j := 0; j < len(rest); j++ {
			c := rest[j]
			switch {
			case quote != 0:
				if c == quote {
					quote = 0
				}
			case c == '"' || c == '\'':
				quote = c
			case c == '>':
				end = j
			}
			if end >= 0 {
				break
			}
		}
		if end < 0 {
			return append(tags, rest)
		}
		tags = append(tags, rest[:end])
		rest = rest[end+1:]
	}
}

// indexTag finds a tag opener that is really one: "<img" and not "<images".
func indexTag(html, tag string) int {
	from := 0
	for {
		i := strings.Index(strings.ToLower(html[from:]), tag)
		if i < 0 {
			return -1
		}
		i += from
		next := i + len(tag)
		if next >= len(html) || next < len(html) && isTagBreak(html[next]) {
			return i
		}
		from = next
	}
}

func isTagBreak(c byte) bool {
	return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '/' || c == '>'
}

// attributes reads a tag's attributes into a map, lowercasing the names.
func attributes(tag string) map[string]string {
	out := map[string]string{}
	i := 0
	for i < len(tag) {
		for i < len(tag) && (tag[i] == ' ' || tag[i] == '\t' || tag[i] == '\n' || tag[i] == '\r') {
			i++
		}
		start := i
		for i < len(tag) && tag[i] != '=' && tag[i] != ' ' && tag[i] != '\t' && tag[i] != '\n' && tag[i] != '\r' {
			i++
		}
		name := strings.ToLower(tag[start:i])
		if name == "" {
			i++
			continue
		}
		for i < len(tag) && (tag[i] == ' ' || tag[i] == '\t') {
			i++
		}
		if i >= len(tag) || tag[i] != '=' {
			out[name] = "" // a bare attribute, e.g. `loading`
			continue
		}
		i++ // past '='
		for i < len(tag) && (tag[i] == ' ' || tag[i] == '\t') {
			i++
		}
		if i >= len(tag) {
			break
		}
		var value string
		if q := tag[i]; q == '"' || q == '\'' {
			i++
			start = i
			for i < len(tag) && tag[i] != q {
				i++
			}
			value = tag[start:i]
			i++ // past the closing quote
		} else {
			start = i
			for i < len(tag) && tag[i] != ' ' && tag[i] != '\t' && tag[i] != '\n' && tag[i] != '\r' {
				i++
			}
			value = tag[start:i]
		}
		out[name] = unescape(strings.TrimSpace(value))
	}
	return out
}

// firstAttr picks the first attribute that carries text.
func firstAttr(attrs map[string]string, names ...string) string {
	for _, name := range names {
		if v := strings.TrimSpace(attrs[name]); v != "" {
			return v
		}
	}
	return ""
}

// unescape resolves the handful of entities an alt text realistically carries.
// Anything else is left as written: this text is quoted back to a reader as
// the page's own words, and a half-decoded entity is less confusing than a
// wrong guess at one.
func unescape(s string) string {
	if !strings.Contains(s, "&") {
		return s
	}
	return strings.NewReplacer(
		"&amp;", "&", "&lt;", "<", "&gt;", ">", "&quot;", `"`,
		"&#39;", "'", "&apos;", "'", "&nbsp;", " ",
	).Replace(s)
}

// withAlts folds the alt texts into the image list and appends the images only
// the HTML knew about, keeping the provider's order for the ones it listed.
func withAlts(images []Image, html string) []Image {
	if strings.TrimSpace(html) == "" {
		return images
	}
	alts := imageAlts(html)

	seen := make(map[string]bool, len(images))
	out := make([]Image, 0, len(images)+len(alts))
	for _, img := range images {
		if img.URL == "" || seen[img.URL] {
			continue
		}
		seen[img.URL] = true
		if img.Alt == "" {
			img.Alt = alts[img.URL]
		}
		out = append(out, img)
	}
	for _, tag := range imgTags(html) {
		src := firstAttr(attributes(tag), "src", "data-src", "data-original")
		if src == "" || seen[src] {
			continue
		}
		seen[src] = true
		out = append(out, Image{URL: src, Alt: alts[src]})
	}
	return out
}
