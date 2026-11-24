package provider

import (
	"os"
	"path/filepath"
	"testing"
)

// TestDocsUpToDate fails when docs/ does not match the schemas; run `go run ./tools/docgen`.
func TestDocsUpToDate(t *testing.T) {
	root := filepath.Join("..", "..")
	files, err := GenerateDocs(filepath.Join(root, "examples"))
	if err != nil {
		t.Fatal(err)
	}
	if len(files) != 1+10+3 {
		t.Fatalf("expected 14 doc pages, got %d", len(files))
	}
	for name, want := range files {
		got, err := os.ReadFile(filepath.Join(root, "docs", name))
		if err != nil || string(got) != want {
			t.Errorf("docs/%s is stale; run `go run ./tools/docgen`", name)
		}
	}
}
