package pkg

import (
	"encoding/json"
	"fmt"
	"testing"
)

// Test file: uses json.Marshal and fmt.Printf, but these must be EXCLUDED by
// exclude_tests. Naive mode (no excludes) would wrongly count them.
func TestSerialize(t *testing.T) {
	b, _ := json.Marshal(map[string]int{"a": 1})
	fmt.Printf("got %s\n", b)
}
