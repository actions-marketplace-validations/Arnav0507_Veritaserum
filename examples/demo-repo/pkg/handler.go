package pkg

import "example.com/demo/base"

func Serialize(v any) ([]byte, error) {
	return base.JSONMarshal(v)
}
