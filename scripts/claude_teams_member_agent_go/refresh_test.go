package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

// withTokenURL points the refresh call at a test server for the
// duration of one test.
func withTokenURL(t *testing.T, url string) {
	t.Helper()
	previous := anthropicTokenURLForTest
	anthropicTokenURLForTest = url
	t.Cleanup(func() { anthropicTokenURLForTest = previous })
}

func writeCredentials(t *testing.T, contents string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), ".credentials.json")
	if err := os.WriteFile(path, []byte(contents), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func msFromNow(d time.Duration) int64 {
	return time.Now().Add(d).UnixMilli()
}

// --- the safety rule -------------------------------------------------

// The entire justification for refreshing at all is that an expired
// token means the member's own Claude Code CLI is not currently using
// it. Refresh tokens rotate, so touching a LIVE token could log the
// member out of the CLI they are actively working in. A still-valid
// token must therefore never be refreshed.
func TestLiveTokenIsNeverRefreshed(t *testing.T) {
	refreshCalled := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		refreshCalled = true
	}))
	defer server.Close()
	withTokenURL(t, server.URL)

	path := writeCredentials(t, `{"claudeAiOauth":{"accessToken":"live-token","refreshToken":"refresh-abc","expiresAt":`+
		itoa(msFromNow(8*time.Hour))+`}}`)

	app := &appContext{credentialsPath: path}
	token, err := app.resolveLocalAccessToken()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if refreshCalled {
		t.Fatal("a still-valid token must never be refreshed: rotation could log the member's own CLI out")
	}
	if token != "live-token" {
		t.Fatalf("token = %q, want the existing live token", token)
	}
}

// A token minutes from lapsing is treated as expired, because the
// usage request that follows takes non-zero time.
func TestTokenAboutToExpireIsRefreshed(t *testing.T) {
	refreshCalled := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		refreshCalled = true
		json.NewEncoder(w).Encode(map[string]interface{}{
			"access_token": "fresh-token", "refresh_token": "refresh-def", "expires_in": 28800,
		})
	}))
	defer server.Close()
	withTokenURL(t, server.URL)

	path := writeCredentials(t, `{"claudeAiOauth":{"accessToken":"stale","refreshToken":"refresh-abc","expiresAt":`+
		itoa(msFromNow(30*time.Second))+`}}`)

	app := &appContext{credentialsPath: path}
	token, _ := app.resolveLocalAccessToken()
	if !refreshCalled || token != "fresh-token" {
		t.Fatalf("a token within the leeway window should be refreshed; called=%v token=%q", refreshCalled, token)
	}
}

// An unknown expiry must not trigger a refresh: guessing could rotate a
// credential a live CLI is relying on.
func TestUnknownExpiryIsNotRefreshed(t *testing.T) {
	refreshCalled := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		refreshCalled = true
	}))
	defer server.Close()
	withTokenURL(t, server.URL)

	path := writeCredentials(t, `{"claudeAiOauth":{"accessToken":"tok","refreshToken":"refresh-abc"}}`)

	app := &appContext{credentialsPath: path}
	token, err := app.resolveLocalAccessToken()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if refreshCalled {
		t.Fatal("an unknown expiry must be treated as not-expired, not refreshed on a guess")
	}
	if token != "tok" {
		t.Fatalf("token = %q, want the existing token", token)
	}
}

// --- the happy path --------------------------------------------------

func TestExpiredTokenIsRefreshedAndPersisted(t *testing.T) {
	var gotBody oauthRefreshRequest
	var gotContentType string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotContentType = r.Header.Get("Content-Type")
		json.NewDecoder(r.Body).Decode(&gotBody)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"access_token": "fresh-token", "refresh_token": "rotated-refresh", "expires_in": 28800,
		})
	}))
	defer server.Close()
	withTokenURL(t, server.URL)

	path := writeCredentials(t, `{"claudeAiOauth":{"accessToken":"expired","refreshToken":"refresh-abc","expiresAt":`+
		itoa(msFromNow(-time.Hour))+`}}`)

	app := &appContext{credentialsPath: path}
	token, err := app.resolveLocalAccessToken()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if token != "fresh-token" {
		t.Fatalf("token = %q, want the refreshed one", token)
	}

	// The CLI sends JSON, not form encoding; mirroring it exactly is
	// what makes this the same OAuth client the CLI uses.
	if !strings.Contains(gotContentType, "application/json") {
		t.Fatalf("Content-Type = %q, want application/json", gotContentType)
	}
	if gotBody.GrantType != "refresh_token" || gotBody.RefreshToken != "refresh-abc" {
		t.Fatalf("unexpected refresh request body: %+v", gotBody)
	}
	if gotBody.ClientID != anthropicOAuthClientID {
		t.Fatalf("client_id = %q, want Claude Code's own", gotBody.ClientID)
	}

	// Persisted, so the member's CLI benefits and the next run doesn't
	// spend another refresh.
	saved, _ := readLocalCredentials(path)
	if saved.AccessToken != "fresh-token" {
		t.Fatalf("saved accessToken = %q, want the refreshed one", saved.AccessToken)
	}
	// Refresh tokens rotate: storing the old one would make the next
	// refresh fail.
	if saved.RefreshToken != "rotated-refresh" {
		t.Fatalf("saved refreshToken = %q, want the rotated one", saved.RefreshToken)
	}
	if saved.isExpired(time.Now()) {
		t.Fatal("the stored expiry should now be in the future")
	}
}

// The credentials file belongs to the Claude Code CLI and carries
// fields this agent does not model. Clobbering them would break the
// member's CLI far worse than the expired token being fixed.
func TestRefreshPreservesUnknownCredentialFields(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"access_token": "fresh-token", "refresh_token": "rotated", "expires_in": 28800,
		})
	}))
	defer server.Close()
	withTokenURL(t, server.URL)

	original := `{"claudeAiOauth":{"accessToken":"expired","refreshToken":"refresh-abc","expiresAt":` +
		itoa(msFromNow(-time.Hour)) +
		`,"refreshTokenExpiresAt":1799999999999,"scopes":["user:inference","user:profile"],` +
		`"subscriptionType":"team","rateLimitTier":"default"},"trustedDeviceToken":"device-xyz"}`
	path := writeCredentials(t, original)

	app := &appContext{credentialsPath: path}
	if _, err := app.resolveLocalAccessToken(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	data, _ := os.ReadFile(path)
	var raw map[string]interface{}
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("credentials file is no longer valid JSON: %v", err)
	}
	if raw["trustedDeviceToken"] != "device-xyz" {
		t.Fatalf("top-level unknown field was lost: %v", raw["trustedDeviceToken"])
	}
	oauth := raw["claudeAiOauth"].(map[string]interface{})
	for key, want := range map[string]interface{}{
		"subscriptionType": "team",
		"rateLimitTier":    "default",
	} {
		if oauth[key] != want {
			t.Fatalf("unknown field %q was lost or changed: %v", key, oauth[key])
		}
	}
	if oauth["refreshTokenExpiresAt"] == nil {
		t.Fatal("refreshTokenExpiresAt was lost")
	}
	scopes, ok := oauth["scopes"].([]interface{})
	if !ok || len(scopes) != 2 {
		t.Fatalf("scopes were lost or mangled: %v", oauth["scopes"])
	}
}

// The file holds bearer credentials; a refresh must not loosen its
// permissions.
func TestRefreshKeepsCredentialsFilePrivate(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"access_token": "fresh", "refresh_token": "rotated", "expires_in": 28800,
		})
	}))
	defer server.Close()
	withTokenURL(t, server.URL)

	path := writeCredentials(t, `{"claudeAiOauth":{"accessToken":"expired","refreshToken":"r","expiresAt":`+
		itoa(msFromNow(-time.Hour))+`}}`)

	app := &appContext{credentialsPath: path}
	app.resolveLocalAccessToken()

	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if perm := info.Mode().Perm(); perm&0o077 != 0 {
		t.Fatalf("credentials file is group/world accessible after refresh: %v", perm)
	}
}

// --- failure handling ------------------------------------------------

// A refresh failure must leave the run no worse off than before this
// feature existed: carry on with the expired token and let the usual
// 401 path report auth_error.
func TestRefreshFailureFallsBackToExpiredToken(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		w.Write([]byte(`{"error":"invalid_grant"}`))
	}))
	defer server.Close()
	withTokenURL(t, server.URL)

	original := `{"claudeAiOauth":{"accessToken":"expired-token","refreshToken":"refresh-abc","expiresAt":` +
		itoa(msFromNow(-time.Hour)) + `}}`
	path := writeCredentials(t, original)

	app := &appContext{credentialsPath: path}
	token, err := app.resolveLocalAccessToken()
	if err != nil {
		t.Fatalf("a refresh failure must not be fatal, got %v", err)
	}
	if token != "expired-token" {
		t.Fatalf("token = %q, want the existing expired token as fallback", token)
	}

	// A failed refresh must not have damaged the file.
	after, _ := os.ReadFile(path)
	if string(after) != original {
		t.Fatalf("credentials file was modified by a failed refresh:\n%s", after)
	}
	if _, err := os.Stat(path + ".tmp"); !os.IsNotExist(err) {
		t.Fatal("a temp file was left behind")
	}
}

// Nothing to refresh with: fall through rather than failing the run.
func TestExpiredTokenWithNoRefreshTokenFallsThrough(t *testing.T) {
	refreshCalled := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		refreshCalled = true
	}))
	defer server.Close()
	withTokenURL(t, server.URL)

	path := writeCredentials(t, `{"claudeAiOauth":{"accessToken":"expired","expiresAt":`+
		itoa(msFromNow(-time.Hour))+`}}`)

	app := &appContext{credentialsPath: path}
	token, err := app.resolveLocalAccessToken()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if refreshCalled {
		t.Fatal("must not attempt a refresh with no refresh token")
	}
	if token != "expired" {
		t.Fatalf("token = %q, want the existing token", token)
	}
}

// The refresh endpoint's error bodies can echo token material, so
// errors from it must carry the status code only -- these strings reach
// stderr and, via fetch_error_message, server-side storage.
func TestRefreshErrorsDoNotLeakResponseBodies(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		w.Write([]byte(`{"error":"invalid_grant","refresh_token":"sk-ant-super-secret-value"}`))
	}))
	defer server.Close()

	_, status, err := refreshAnthropicTokenAt(server.URL, "refresh-abc")
	if err == nil {
		t.Fatal("expected an error for a 401")
	}
	if status != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", status)
	}
	if strings.Contains(err.Error(), "sk-ant-super-secret-value") {
		t.Fatalf("error leaked response body content: %v", err)
	}
}

func TestRefreshRejectsResponseWithoutAccessToken(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"expires_in": 28800}`))
	}))
	defer server.Close()

	if _, _, err := refreshAnthropicTokenAt(server.URL, "refresh-abc"); err == nil {
		t.Fatal("a response with no access_token must be an error, not a silent empty token")
	}
}

// An absent refresh_token in the response means "keep the current one";
// storing an empty string would break the next refresh.
func TestAbsentRotatedRefreshTokenKeepsTheExistingOne(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"access_token": "fresh", "expires_in": 28800,
		})
	}))
	defer server.Close()
	withTokenURL(t, server.URL)

	path := writeCredentials(t, `{"claudeAiOauth":{"accessToken":"expired","refreshToken":"keep-me","expiresAt":`+
		itoa(msFromNow(-time.Hour))+`}}`)

	app := &appContext{credentialsPath: path}
	if _, err := app.resolveLocalAccessToken(); err != nil {
		t.Fatal(err)
	}

	saved, _ := readLocalCredentials(path)
	if saved.RefreshToken != "keep-me" {
		t.Fatalf("refreshToken = %q, want the existing one preserved", saved.RefreshToken)
	}
}

// TestAgainstRealCredentialsFileShape uses the exact field set observed
// in a real ~/.claude/.credentials.json written by Claude Code, rather
// than the minimal fixtures above, so a mismatch between the shape this
// agent assumes and the shape the CLI actually writes is caught here
// rather than on a member's machine.
func TestAgainstRealCredentialsFileShape(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 28800,
		})
	}))
	defer server.Close()
	withTokenURL(t, server.URL)

	realShape := map[string]interface{}{
		"claudeAiOauth": map[string]interface{}{
			"accessToken":           "sk-ant-oat-EXPIRED",
			"refreshToken":          "sk-ant-ort-OLD",
			"expiresAt":             time.Now().Add(-2 * time.Hour).UnixMilli(),
			"refreshTokenExpiresAt": time.Now().Add(700 * time.Hour).UnixMilli(),
			"scopes": []string{
				"user:file_upload", "user:inference", "user:mcp_servers",
				"user:profile", "user:sessions:claude_code",
			},
			"subscriptionType": "team",
			"rateLimitTier":    "default_claude_code_20250519",
		},
		"trustedDeviceToken": "tdt-abc",
	}
	data, _ := json.MarshalIndent(realShape, "", "  ")
	path := filepath.Join(t.TempDir(), ".credentials.json")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}

	app := &appContext{credentialsPath: path}
	token, err := app.resolveLocalAccessToken()
	if err != nil {
		t.Fatalf("a real-shaped credentials file failed: %v", err)
	}
	if token != "new-access" {
		t.Fatalf("token = %q, want the refreshed one", token)
	}

	after, _ := os.ReadFile(path)
	var out map[string]interface{}
	if err := json.Unmarshal(after, &out); err != nil {
		t.Fatalf("the CLI's file is no longer valid JSON after our write: %v", err)
	}
	oauth := out["claudeAiOauth"].(map[string]interface{})
	if oauth["accessToken"] != "new-access" || oauth["refreshToken"] != "new-refresh" {
		t.Fatalf("tokens were not updated: %v", oauth)
	}
	// Everything the CLI needs and this agent does not model must
	// survive: losing any of these breaks the member's own CLI.
	for _, key := range []string{"refreshTokenExpiresAt", "scopes", "subscriptionType", "rateLimitTier"} {
		if oauth[key] == nil {
			t.Fatalf("real CLI field %q was destroyed by the refresh write", key)
		}
	}
	if out["trustedDeviceToken"] != "tdt-abc" {
		t.Fatal("trustedDeviceToken was destroyed by the refresh write")
	}
}

func itoa(v int64) string { return strconv.FormatInt(v, 10) }
