package base

func Infof(format string, args ...any) {
	_ = format
	_ = args
}

func JSONMarshal(v any) ([]byte, error) {
	_ = v
	return []byte("{}"), nil
}
