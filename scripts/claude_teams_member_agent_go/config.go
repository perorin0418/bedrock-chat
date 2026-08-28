package main

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"
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

// credentialsFile mirrors only the one field this agent needs out of
// %USERPROFILE%\.claude\.credentials.json (the local Claude Code CLI's
// own login state, used ONLY for the usage-limit tracking lookup --
// unrelated to the chat-side token, which comes from pasting `claude
// setup-token` output at first run instead. See main package docs and
// docs/CLAUDE_TEAMS_OAUTH.md).
type credentialsFile struct {
	ClaudeAiOauth struct {
		AccessToken string `json:"accessToken"`
	} `json:"claudeAiOauth"`
}

func readLocalAccessToken(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return "", fmt.Errorf("Claude Code credentials file not found at: %s", path)
		}
		return "", err
	}
	var creds credentialsFile
	if err := json.Unmarshal(data, &creds); err != nil {
		return "", fmt.Errorf("parsing %s: %w", path, err)
	}
	if creds.ClaudeAiOauth.AccessToken == "" {
		return "", fmt.Errorf("no claudeAiOauth.accessToken found in %s", path)
	}
	return creds.ClaudeAiOauth.AccessToken, nil
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
