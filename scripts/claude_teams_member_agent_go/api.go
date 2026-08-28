package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
)

var httpClient = &http.Client{Timeout: requestTimeout}

func doJSONRequest(method, uri string, body interface{}, headers map[string]string, out interface{}) (int, error) {
	var reqBody io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return 0, err
		}
		reqBody = bytes.NewReader(data)
	}

	req, err := http.NewRequest(method, uri, reqBody)
	if err != nil {
		return 0, err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}

	resp, err := httpClient.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()

	respData, err := io.ReadAll(resp.Body)
	if err != nil {
		return resp.StatusCode, err
	}

	if resp.StatusCode >= 400 {
		return resp.StatusCode, fmt.Errorf("HTTP %d: %s", resp.StatusCode, strings200(respData))
	}

	if out != nil && len(respData) > 0 {
		if err := json.Unmarshal(respData, out); err != nil {
			return resp.StatusCode, fmt.Errorf("parsing response: %w", err)
		}
	}
	return resp.StatusCode, nil
}

func strings200(b []byte) string {
	const max = 500
	if len(b) > max {
		return string(b[:max]) + "..."
	}
	return string(b)
}

// --- bedrock-chat API calls ---
// Every endpoint, request/response shape below is unchanged from the
// .ps1 version -- see backend/app/routes/claude_teams_ingest.py and
// docs/CLAUDE_TEAMS_OAUTH.md, which document the full trust model
// (org-wide registration_secret vs per-token ingest_secret) this
// mirrors exactly.

type registerRequest struct {
	RegistrationSecret string `json:"registration_secret"`
	DisplayName        string `json:"display_name"`
	TokenValue         string `json:"token_value"`
}

type registerResponse struct {
	// Note: bedrock-chat's schemas serialize responses via
	// humps.camelize (see backend/app/routes/schemas/base.py) --
	// tokenId/ingestSecret, NOT the snake_case used in the request
	// body. This bit the .ps1 predecessor once (silently read $null);
	// documented here so it isn't repeated.
	TokenID      string `json:"tokenId"`
	IngestSecret string `json:"ingestSecret"`
}

func (a *appContext) registerToken(displayName, tokenValue string) (*registerResponse, error) {
	reqBody := registerRequest{
		RegistrationSecret: a.registrationSecret,
		DisplayName:        displayName,
		TokenValue:         tokenValue,
	}
	var resp registerResponse
	_, err := doJSONRequest(http.MethodPost, a.apiEndpoint+"/claude-teams-tokens/register", reqBody, nil, &resp)
	if err != nil {
		return nil, err
	}
	return &resp, nil
}

type usageSnapshotRequest struct {
	IngestSecret         string   `json:"ingest_secret"`
	FetchStatus          string   `json:"fetch_status"`
	FetchErrorMessage    *string  `json:"fetch_error_message,omitempty"`
	FiveHourUtilization  *float64 `json:"five_hour_utilization,omitempty"`
	FiveHourResetsAt     *string  `json:"five_hour_resets_at,omitempty"`
	SevenDayUtilization  *float64 `json:"seven_day_utilization,omitempty"`
	SevenDayResetsAt     *string  `json:"seven_day_resets_at,omitempty"`
	SampledAtMs          *int64   `json:"sampled_at_ms,omitempty"`
}

func (a *appContext) ingestUsageSnapshot(tokenID, ingestSecret string, req usageSnapshotRequest) error {
	req.IngestSecret = ingestSecret
	uri := fmt.Sprintf("%s/claude-teams-tokens/%s/usage-snapshot", a.apiEndpoint, url.PathEscape(tokenID))
	_, err := doJSONRequest(http.MethodPost, uri, req, nil, nil)
	return err
}

type tokenStatusResponse struct {
	Enabled bool `json:"enabled"`
}

// getChatTokenStatus asks bedrock-chat whether the chat-side token this
// machine registered (the one pasted from `claude setup-token` output,
// held server-side only) is still usable. Returns (enabled, ok): ok is
// false when the check itself couldn't be completed (network blip,
// 5xx, ...) and must NOT be treated as "disabled" -- a transient
// outage must not nag every member hourly. See
// docs/CLAUDE_TEAMS_OAUTH.md's "Chat-token expiry detection" section.
func (a *appContext) getChatTokenStatus(tokenID, ingestSecret string) (enabled bool, ok bool) {
	uri := fmt.Sprintf("%s/claude-teams-tokens/%s/status?ingest_secret=%s",
		a.apiEndpoint, url.PathEscape(tokenID), url.QueryEscape(ingestSecret))
	var resp tokenStatusResponse
	_, err := doJSONRequest(http.MethodGet, uri, nil, nil, &resp)
	if err != nil {
		warnf("could not check chat-token status with bedrock-chat: %v", err)
		return false, false
	}
	return resp.Enabled, true
}

// anthropicUsageResponse mirrors Anthropic's undocumented
// /api/oauth/usage response shape, unchanged from the .ps1 version.
type anthropicUsageResponse struct {
	FiveHour struct {
		Utilization float64 `json:"utilization"`
		ResetsAt    string  `json:"resets_at"`
	} `json:"five_hour"`
	SevenDay struct {
		Utilization float64 `json:"utilization"`
		ResetsAt    string  `json:"resets_at"`
	} `json:"seven_day"`
}

func fetchAnthropicUsage(accessToken string) (*anthropicUsageResponse, int, error) {
	req, err := http.NewRequest(http.MethodGet, usageAPIURL, nil)
	if err != nil {
		return nil, 0, err
	}
	req.Header.Set("Authorization", "Bearer "+accessToken)
	req.Header.Set("anthropic-version", anthropicVersion)
	req.Header.Set("anthropic-beta", oauthBetaHeader)

	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, resp.StatusCode, err
	}
	if resp.StatusCode >= 400 {
		return nil, resp.StatusCode, fmt.Errorf("HTTP %d: %s", resp.StatusCode, strings200(data))
	}
	var out anthropicUsageResponse
	if err := json.Unmarshal(data, &out); err != nil {
		return nil, resp.StatusCode, fmt.Errorf("parsing usage response: %w", err)
	}
	return &out, resp.StatusCode, nil
}
