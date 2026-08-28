package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestRegisterTokenSendsExpectedRequestAndParsesCamelCaseResponse(t *testing.T) {
	var gotBody registerRequest
	var gotMethod, gotPath string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod = r.Method
		gotPath = r.URL.Path
		if err := json.NewDecoder(r.Body).Decode(&gotBody); err != nil {
			t.Errorf("decoding request body: %v", err)
		}
		// Confirms this Go client reads the API's actual response
		// shape correctly: bedrock-chat's schemas camelize responses
		// (tokenId/ingestSecret), NOT the snake_case used in the
		// request body -- this exact mismatch silently broke the .ps1
		// predecessor once (see register.go/api.go comments).
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"tokenId":"tok-999","ingestSecret":"secret-999"}`))
	}))
	defer server.Close()

	app := &appContext{apiEndpoint: server.URL, registrationSecret: "the-reg-secret"}
	resp, err := app.registerToken("member@example.com", "sk-ant-oat-fake")
	if err != nil {
		t.Fatalf("registerToken: %v", err)
	}

	if gotMethod != http.MethodPost {
		t.Errorf("want POST, got %s", gotMethod)
	}
	if gotPath != "/claude-teams-tokens/register" {
		t.Errorf("want /claude-teams-tokens/register, got %s", gotPath)
	}
	if gotBody.RegistrationSecret != "the-reg-secret" || gotBody.DisplayName != "member@example.com" || gotBody.TokenValue != "sk-ant-oat-fake" {
		t.Errorf("unexpected request body: %+v", gotBody)
	}
	if resp.TokenID != "tok-999" || resp.IngestSecret != "secret-999" {
		t.Errorf("unexpected parsed response: %+v", resp)
	}
}

func TestRegisterTokenPropagates401AsError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"detail":"Invalid registration secret."}`))
	}))
	defer server.Close()

	app := &appContext{apiEndpoint: server.URL, registrationSecret: "wrong"}
	_, err := app.registerToken("member@example.com", "sk-ant-oat-fake")
	if err == nil {
		t.Fatal("expected an error for a 401 response")
	}
	if !strings.Contains(err.Error(), "401") {
		t.Errorf("expected error to mention HTTP 401, got: %v", err)
	}
}

func TestIngestUsageSnapshotEscapesTokenIDAndSendsIngestSecret(t *testing.T) {
	var gotEscapedPath string
	var gotBody usageSnapshotRequest
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// r.URL.Path is always decoded by net/http regardless of what
		// was sent on the wire, so it can't tell us whether the client
		// actually escaped anything -- EscapedPath() (or the raw
		// RequestURI) reflects what was literally on the request line.
		gotEscapedPath = r.URL.EscapedPath()
		if err := json.NewDecoder(r.Body).Decode(&gotBody); err != nil {
			t.Errorf("decoding request body: %v", err)
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()

	app := &appContext{apiEndpoint: server.URL}
	fiveHour := 42.0
	err := app.ingestUsageSnapshot("tok/with slash", "ingest-secret-value", usageSnapshotRequest{
		FetchStatus:         "ok",
		FiveHourUtilization: &fiveHour,
	})
	if err != nil {
		t.Fatalf("ingestUsageSnapshot: %v", err)
	}
	// url.PathEscape turns "/" into "%2F" and " " into "%20" -- confirms
	// a token_id containing characters special to URL paths does not
	// corrupt the request path on the wire (net/http transparently
	// decodes r.URL.Path back for handlers, which is why this asserts
	// against EscapedPath() instead).
	if !strings.Contains(gotEscapedPath, "tok%2Fwith%20slash") {
		t.Errorf("expected escaped token_id in the request line, got %s", gotEscapedPath)
	}
	if gotBody.IngestSecret != "ingest-secret-value" {
		t.Errorf("expected ingest_secret to be set on the request body, got %+v", gotBody)
	}
	if gotBody.FetchStatus != "ok" || gotBody.FiveHourUtilization == nil || *gotBody.FiveHourUtilization != 42.0 {
		t.Errorf("unexpected request body: %+v", gotBody)
	}
}

func TestGetChatTokenStatusReturnsEnabledTrue(t *testing.T) {
	var gotQuery string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.RawQuery
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"enabled":true}`))
	}))
	defer server.Close()

	app := &appContext{apiEndpoint: server.URL}
	enabled, ok := app.getChatTokenStatus("tok-1", "secret with spaces&special")
	if !ok {
		t.Fatal("expected ok=true for a successful response")
	}
	if !enabled {
		t.Fatal("expected enabled=true")
	}
	if !strings.Contains(gotQuery, "ingest_secret=secret+with+spaces%26special") {
		t.Errorf("expected properly escaped ingest_secret in query, got %s", gotQuery)
	}
}

func TestGetChatTokenStatusReturnsEnabledFalse(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"enabled":false}`))
	}))
	defer server.Close()

	app := &appContext{apiEndpoint: server.URL}
	enabled, ok := app.getChatTokenStatus("tok-1", "secret")
	if !ok {
		t.Fatal("expected ok=true for a successful response")
	}
	if enabled {
		t.Fatal("expected enabled=false")
	}
}

// TestGetChatTokenStatusNetworkFailureReturnsNotOK asserts a transient
// failure (here: server unreachable) returns ok=false, NOT
// enabled=false -- the critical distinction documented in
// getChatTokenStatus's comments: a network blip must never be
// misread as "the token was disabled" and trigger a false re-register/
// notification.
func TestGetChatTokenStatusNetworkFailureReturnsNotOK(t *testing.T) {
	app := &appContext{apiEndpoint: "http://127.0.0.1:1"} // nothing listens here
	enabled, ok := app.getChatTokenStatus("tok-1", "secret")
	if ok {
		t.Fatal("expected ok=false when the request itself fails")
	}
	if enabled {
		t.Fatal("expected enabled=false (zero value) when ok=false")
	}
}

func TestGetChatTokenStatus401ReturnsNotOK(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"detail":"Invalid token_id or ingest_secret."}`))
	}))
	defer server.Close()

	app := &appContext{apiEndpoint: server.URL}
	_, ok := app.getChatTokenStatus("tok-1", "wrong-secret")
	if ok {
		t.Fatal("expected ok=false for a 401 (bad ingest_secret) response")
	}
}
