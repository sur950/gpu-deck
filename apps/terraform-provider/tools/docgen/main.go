// docgen writes docs/ (Terraform registry layout) from the provider schemas and examples/.
//
//	go run ./tools/docgen        # from apps/terraform-provider
package main

import (
	"log"
	"os"
	"path/filepath"

	"github.com/sur950/gpu-deck/apps/terraform-provider/internal/provider"
)

func main() {
	files, err := provider.GenerateDocs("examples")
	if err != nil {
		log.Fatal(err)
	}
	if err := os.RemoveAll("docs"); err != nil {
		log.Fatal(err)
	}
	for name, content := range files {
		path := filepath.Join("docs", name)
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			log.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
			log.Fatal(err)
		}
	}
	log.Printf("wrote %d files to docs/", len(files))
}
