package main

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"
	"time"
)

// localConfig mirrors the JSON shape the .ps1 version wrote (and what
// this .exe must keep reading so an already-registered member's
// existing config file continues to work with zero re-registration):
// snake_case token_id/ingest_secret. See docs/CLAUDE_TEAMS_OAUTH.md.
type localConfig struct {
	TokenID      string `json:"token_id"`
	IngestSecret string `json:"ingest_secret"`
}

func readLocalConfig(path string) (*localConfig, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("reading %s: %w", path, err)
	}
	var cfg localConfig
	if err := json.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("parsing %s: %w", path, err)
	}
	return &cfg, nil
}

func writeLocalConfig(path string, cfg localConfig) error {
	dir := parentDir(path)
	if dir != "" {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			return fmt.Errorf("creating config directory %s: %w", dir, err)
		}
	}
	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	// 0o600: same-user-only, matching the local Claude Code CLI's own
	// .credentials.json permissions convention -- this file's
	// ingest_secret is a bearer credential.
	return os.WriteFile(path, data, 0o600)
}

// credentialsFile mirrors the fields this agent needs out of
// %USERPROFILE%\.claude\.credentials.json (the local Claude Code CLI's
// own login state, used ONLY for the usage-limit tracking lookup --
// unrelated to the chat-side token, which comes from pasting `claude
// setup-token` output at first run instead. See main package docs and
// docs/CLAUDE_TEAMS_OAUTH.md).
//
// Read-only as far as this struct is concerned: writing a refreshed
// token back goes through writeRefreshedAccessToken, which edits the
// raw JSON instead of re-serializing this struct, precisely so the
// fields listed here staying a subset of the real file is safe. See
// that function for why that matters.
type credentialsFile struct {
	ClaudeAiOauth struct {
		AccessToken  string `json:"accessToken"`
		RefreshToken string `json:"refreshToken"`
		// Epoch milliseconds. Claude Code writes this alongside the
		// token; an absent/zero value is treated as "unknown", which
		// this agent deliberately reads as not-yet-expired (see
		// localCredentials.isExpired).
		ExpiresAt int64 `json:"expiresAt"`
	} `json:"claudeAiOauth"`
}

// localCredentials is what the rest of the program works with: the
// local Claude Code login's access token, the refresh token needed to
// renew it, and when the access token lapses.
type localCredentials struct {
	AccessToken  string
	RefreshToken string
	// Epoch milliseconds, or 0 when the file didn't say.
	ExpiresAt int64
}

// expiryLeeway treats a token that is about to lapse as already
// lapsed. Without it, a token with seconds left passes the check here
// and then fails at Anthropic moments later, costing this run its
// usage sample for no reason -- the request, and any retry inside it,
// takes non-zero time.
const expiryLeeway = 2 * time.Minute

// isExpired reports whether the access token has lapsed (or is about
// to).
//
// An unknown expiry (no expiresAt in the file) is deliberately treated
// as NOT expired. This is the conservative direction: the only thing
// this predicate gates is whether to spend the refresh token (see
// refreshLocalCredentials), and refreshing on a guess would rotate a
// credential the member's own Claude Code CLI is relying on. Being
// wrong the other way merely means one 401, which the caller already
// handles.
func (c localCredentials) isExpired(now time.Time) bool {
	if c.ExpiresAt == 0 {
		return false
	}
	return now.Add(expiryLeeway).UnixMilli() >= c.ExpiresAt
}

func readLocalCredentials(path string) (*localCredentials, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, fmt.Errorf("Claude Code credentials file not found at: %s", path)
		}
		return nil, err
	}
	var creds credentialsFile
	if err := json.Unmarshal(data, &creds); err != nil {
		return nil, fmt.Errorf("parsing %s: %w", path, err)
	}
	if creds.ClaudeAiOauth.AccessToken == "" {
		return nil, fmt.Errorf("no claudeAiOauth.accessToken found in %s", path)
	}
	return &localCredentials{
		AccessToken:  creds.ClaudeAiOauth.AccessToken,
		RefreshToken: creds.ClaudeAiOauth.RefreshToken,
		ExpiresAt:    creds.ClaudeAiOauth.ExpiresAt,
	}, nil
}

// writeRefreshedCredentials stores a renewed access/refresh token pair
// back into the Claude Code credentials file.
//
// Decoded into a generic map and re-encoded, rather than marshalling
// credentialsFile, because this file belongs to the Claude Code CLI,
// not to this agent. It carries fields this program deliberately does
// not model (refreshTokenExpiresAt, scopes, subscriptionType,
// rateLimitTier, trustedDeviceToken, and whatever Anthropic adds next),
// and re-serializing a narrow struct over it would silently delete
// every one of them -- breaking the member's own CLI far more
// thoroughly than the expired token this is trying to fix. A map
// round-trip preserves unknown keys untouched and edits only the three
// values being renewed.
//
// Written via a temp file + rename so a crash or a full disk cannot
// leave the member with a truncated, unparseable credentials file;
// rename within the same directory is atomic, so the file is either
// the old contents or the new ones, never half of each. Permissions
// are kept at 0600, matching what the CLI itself writes for a file
// holding bearer credentials.
func writeRefreshedCredentials(path string, updated localCredentials) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var raw map[string]interface{}
	if err := json.Unmarshal(data, &raw); err != nil {
		return fmt.Errorf("parsing %s: %w", path, err)
	}

	oauth, ok := raw["claudeAiOauth"].(map[string]interface{})
	if !ok {
		return fmt.Errorf("%s has no claudeAiOauth object to update", path)
	}
	oauth["accessToken"] = updated.AccessToken
	if updated.RefreshToken != "" {
		oauth["refreshToken"] = updated.RefreshToken
	}
	if updated.ExpiresAt != 0 {
		oauth["expiresAt"] = updated.ExpiresAt
	}

	// Indented to match the CLI's own formatting closely enough that a
	// member opening the file doesn't find it mangled into one line.
	encoded, err := json.MarshalIndent(raw, "", "  ")
	if err != nil {
		return err
	}

	tmpPath := path + ".tmp"
	if err := os.WriteFile(tmpPath, encoded, 0o600); err != nil {
		return err
	}
	if err := os.Rename(tmpPath, path); err != nil {
		// Don't leave a stray .tmp behind for the CLI or the next run
		// to trip over.
		_ = os.Remove(tmpPath)
		return err
	}
	return nil
}

// emailAddressPattern extracts oauthAccount.emailAddress out of
// ~/.claude.json via a plain regex rather than a full JSON parse.
//
// This mirrors a deliberate choice already made (and load-bearing) in
// the .ps1 predecessor: that file accumulates large nested
// config/prompt history over time and was observed, in the .ps1
// version, to exceed what PowerShell 5.1's ConvertFrom-Json accepted
// on accounts with enough history. Go's encoding/json has no such
// depth/size limitation, so a full parse would work here -- but a
// regex pull of one known field is kept anyway for the same second
// reason that mattered in the .ps1 version: it is robust to unrelated
// schema changes/corruption elsewhere in this large, semi-undocumented
// file that a strict full-document parse is not.
var emailAddressPattern = regexp.MustCompile(`"emailAddress"\s*:\s*"([^"]*)"`)

func resolveDisplayNameFromClaudeJSON(path string) (string, bool) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", false
	}
	m := emailAddressPattern.FindSubmatch(data)
	if m == nil {
		return "", false
	}
	email := strings.TrimSpace(string(m[1]))
	if email == "" {
		return "", false
	}
	return email, true
}

func parentDir(path string) string {
	idx := strings.LastIndexAny(path, `/\`)
	if idx < 0 {
		return ""
	}
	return path[:idx]
}
