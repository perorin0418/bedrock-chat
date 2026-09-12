package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestReadLocalConfigMissingFileReturnsNil(t *testing.T) {
	cfg, err := readLocalConfig(filepath.Join(t.TempDir(), "does_not_exist.json"))
	if err != nil {
		t.Fatalf("expected no error for missing file, got: %v", err)
	}
	if cfg != nil {
		t.Fatalf("expected nil config for missing file, got: %+v", cfg)
	}
}

func TestWriteThenReadLocalConfigRoundTrips(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sub", "config.json")
	want := localConfig{TokenID: "tok-123", IngestSecret: "secret-abc"}

	if err := writeLocalConfig(path, want); err != nil {
		t.Fatalf("writeLocalConfig: %v", err)
	}
	got, err := readLocalConfig(path)
	if err != nil {
		t.Fatalf("readLocalConfig: %v", err)
	}
	if got == nil || got.TokenID != want.TokenID || got.IngestSecret != want.IngestSecret {
		t.Fatalf("round-trip mismatch: want %+v, got %+v", want, got)
	}
}

// TestReadLocalConfigMatchesPS1JSONShape asserts this Go implementation
// can read the exact JSON shape claude_teams_member_agent.ps1's
// Write-LocalConfig produces (snake_case token_id/ingest_secret, no
// other fields) -- required so a member already registered via the
// .ps1 predecessor switches to this .exe with zero re-registration.
func TestReadLocalConfigMatchesPS1JSONShape(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	// Exact byte shape ConvertTo-Json (PowerShell) produces for the
	// .ps1 predecessor's @{ token_id = ...; ingest_secret = ... }.
	ps1JSON := "{\r\n    \"token_id\":  \"01ABCXYZ\",\r\n    \"ingest_secret\":  \"shh-secret-value\"\r\n}"
	if err := os.WriteFile(path, []byte(ps1JSON), 0o600); err != nil {
		t.Fatalf("writing fixture: %v", err)
	}

	cfg, err := readLocalConfig(path)
	if err != nil {
		t.Fatalf("readLocalConfig: %v", err)
	}
	if cfg.TokenID != "01ABCXYZ" || cfg.IngestSecret != "shh-secret-value" {
		t.Fatalf("did not parse .ps1-shaped config correctly: %+v", cfg)
	}
}

func TestReadLocalCredentialsMissingFile(t *testing.T) {
	_, err := readLocalCredentials(filepath.Join(t.TempDir(), "missing.json"))
	if err == nil {
		t.Fatal("expected an error for a missing credentials file")
	}
}

func TestReadLocalCredentialsExtractsNestedFields(t *testing.T) {
	path := filepath.Join(t.TempDir(), "creds.json")
	// refreshToken and expiresAt are now read too (they gate and enable
	// the expired-only refresh -- see resolveLocalAccessToken).
	data := `{"claudeAiOauth":{"accessToken":"tok-xyz","refreshToken":"refresh-abc","expiresAt":1788864851847}}`
	if err := os.WriteFile(path, []byte(data), 0o600); err != nil {
		t.Fatalf("writing fixture: %v", err)
	}
	creds, err := readLocalCredentials(path)
	if err != nil {
		t.Fatalf("readLocalCredentials: %v", err)
	}
	if creds.AccessToken != "tok-xyz" {
		t.Fatalf("want tok-xyz, got %q", creds.AccessToken)
	}
	if creds.RefreshToken != "refresh-abc" {
		t.Fatalf("want refresh-abc, got %q", creds.RefreshToken)
	}
	if creds.ExpiresAt != 1788864851847 {
		t.Fatalf("want expiresAt 1788864851847, got %d", creds.ExpiresAt)
	}
}

func TestReadLocalCredentialsMissingAccessTokenField(t *testing.T) {
	path := filepath.Join(t.TempDir(), "creds.json")
	if err := os.WriteFile(path, []byte(`{"claudeAiOauth":{}}`), 0o600); err != nil {
		t.Fatalf("writing fixture: %v", err)
	}
	_, err := readLocalCredentials(path)
	if err == nil {
		t.Fatal("expected an error when accessToken is absent")
	}
}

// TestResolveDisplayNameFromClaudeJSONSurvivesLargeUnparseableBlob is
// the direct Go equivalent of the .ps1 predecessor's documented
// real-world failure mode: PowerShell 5.1's ConvertFrom-Json rejected
// a large, deeply-nested real ~/.claude.json outright. This asserts
// the regex-based extraction here still finds emailAddress inside a
// document structurally invalid as JSON (an intentionally-truncated,
// non-parseable blob) -- proving the implementation does not depend on
// the file being well-formed JSON at all, exactly like its
// predecessor.
func TestResolveDisplayNameFromClaudeJSONSurvivesLargeUnparseableBlob(t *testing.T) {
	path := filepath.Join(t.TempDir(), ".claude.json")
	// Deliberately malformed/truncated (unbalanced braces) -- a strict
	// JSON parser would reject this outright.
	blob := `{"projects": {"foo": {"history": [` +
		`{"nested": {"deep": {"oauthAccount": {"emailAddress": "person@example.com", "other": "field"` +
		`}}}}]}}, "truncated": true`
	if err := os.WriteFile(path, []byte(blob), 0o600); err != nil {
		t.Fatalf("writing fixture: %v", err)
	}

	email, ok := resolveDisplayNameFromClaudeJSON(path)
	if !ok {
		t.Fatal("expected to find emailAddress even in a non-well-formed blob")
	}
	if email != "person@example.com" {
		t.Fatalf("want person@example.com, got %q", email)
	}
}

func TestResolveDisplayNameFromClaudeJSONMissingField(t *testing.T) {
	path := filepath.Join(t.TempDir(), ".claude.json")
	if err := os.WriteFile(path, []byte(`{"projects": {}}`), 0o600); err != nil {
		t.Fatalf("writing fixture: %v", err)
	}
	_, ok := resolveDisplayNameFromClaudeJSON(path)
	if ok {
		t.Fatal("expected ok=false when emailAddress is absent")
	}
}

func TestResolveDisplayNameFromClaudeJSONEmptyValue(t *testing.T) {
	path := filepath.Join(t.TempDir(), ".claude.json")
	if err := os.WriteFile(path, []byte(`{"oauthAccount":{"emailAddress":""}}`), 0o600); err != nil {
		t.Fatalf("writing fixture: %v", err)
	}
	_, ok := resolveDisplayNameFromClaudeJSON(path)
	if ok {
		t.Fatal("expected ok=false when emailAddress is an empty string")
	}
}
