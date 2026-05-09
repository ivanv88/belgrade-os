package auth

import (
	"os"
	"strings"
)

type TrustedSet map[string]struct{}

func ParseTrustedUsers(raw string) TrustedSet {
	s := make(TrustedSet)
	for _, part := range strings.Split(raw, ",") {
		if id := strings.TrimSpace(part); id != "" {
			s[id] = struct{}{}
		}
	}
	return s
}

func LoadTrustedUsers() TrustedSet {
	return ParseTrustedUsers(os.Getenv("TRUSTED_USER_IDS"))
}

func (t TrustedSet) Contains(userID string) bool {
	_, ok := t[userID]
	return ok
}
