package pkg

import "encoding/json"

// Serialize really uses encoding/json at runtime — a genuine forbidden violation.
func Serialize(v any) ([]byte, error) {
	return json.Marshal(v)
}
