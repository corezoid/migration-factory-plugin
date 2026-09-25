package firecrawl

import "testing"

// The alt text is the whole point of asking for the markup: the provider's
// image list is addresses, and an address identifies nobody.
func TestWithAltsJoinsTheTextToTheAddress(t *testing.T) {
	html := `<figure><img src="https://acme.test/team/ivan.jpg" alt="Іван Іванов, CTO" loading="lazy"></figure>
	<img src='https://acme.test/logo.png' title='Acme'>
	<img data-src="https://acme.test/office.jpg" alt="Our office in Cluj &amp; the yard">
	<img alt="no address">
	<img src="https://acme.test/team/ivan.jpg" alt="">`

	got := withAlts([]Image{{URL: "https://acme.test/team/ivan.jpg"}}, html)

	want := []Image{
		{URL: "https://acme.test/team/ivan.jpg", Alt: "Іван Іванов, CTO"},
		{URL: "https://acme.test/logo.png", Alt: "Acme"},
		{URL: "https://acme.test/office.jpg", Alt: "Our office in Cluj & the yard"},
	}
	if len(got) != len(want) {
		t.Fatalf("got %d images (%+v), want %d", len(got), got, len(want))
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("image %d = %+v, want %+v", i, got[i], want[i])
		}
	}
}

// A ">" inside an alt text does not end the tag, and an image the provider
// already described keeps its own text.
func TestWithAltsReadsAwkwardMarkup(t *testing.T) {
	html := `<img src="https://acme.test/a.png" alt="Sales > Retail"><img src="https://acme.test/b.png">`

	got := withAlts([]Image{{URL: "https://acme.test/b.png", Alt: "from the provider"}}, html)

	if len(got) != 2 {
		t.Fatalf("got %+v, want both images", got)
	}
	if got[0].Alt != "from the provider" {
		t.Errorf("the provider's own text was overwritten: %+v", got[0])
	}
	if got[1].URL != "https://acme.test/a.png" || got[1].Alt != "Sales > Retail" {
		t.Errorf("image = %+v, want the alt text read past the \">\"", got[1])
	}
}

// Without markup there is nothing to join, and the list is handed back as it
// came rather than emptied.
func TestWithAltsWithoutHTML(t *testing.T) {
	images := []Image{{URL: "https://acme.test/a.png"}}
	if got := withAlts(images, "  "); len(got) != 1 || got[0].URL != images[0].URL {
		t.Errorf("got %+v, want the list unchanged", got)
	}
}
