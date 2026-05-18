package auth

import (
	"testing"
)

func TestParseTrustedUsers_Empty(t *testing.T) {
	s := ParseTrustedUsers("")
	if len(s) != 0 {
		t.Fatalf("expected empty set, got %v", s)
	}
}

func TestParseTrustedUsers_Single(t *testing.T) {
	s := ParseTrustedUsers("user@example.com")
	if !s.Contains("user@example.com") {
		t.Fatal("expected user@example.com to be trusted")
	}
}

func TestParseTrustedUsers_Multiple(t *testing.T) {
	s := ParseTrustedUsers("alice@example.com, bob@example.com ,charlie@example.com")
	for _, id := range []string{"alice@example.com", "bob@example.com", "charlie@example.com"} {
		if !s.Contains(id) {
			t.Fatalf("expected %s to be trusted", id)
		}
	}
}

func TestParseTrustedUsers_WhitespaceTrimmed(t *testing.T) {
	s := ParseTrustedUsers("  alice@example.com  ")
	if !s.Contains("alice@example.com") {
		t.Fatal("whitespace should be trimmed from user IDs")
	}
}

func TestContains_Untrusted(t *testing.T) {
	s := ParseTrustedUsers("alice@example.com")
	if s.Contains("mallory@example.com") {
		t.Fatal("mallory should not be trusted")
	}
}
