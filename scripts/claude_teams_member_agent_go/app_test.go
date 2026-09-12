package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// notification records whether a member would have been interrupted by
// a modal dialog, and with what.
type notification struct {
	title   string
	message string
}

// captureNotifications redirects every a.notify call into a slice for
// the duration of one test, restoring the production behavior after.
func captureNotifications(t *testing.T) *[]notification {
	t.Helper()
	var got []notification
	previous := notifyUser
	notifyUser = func(title, message string) {
		got = append(got, notification{title: title, message: message})
	}
	t.Cleanup(func() { notifyUser = previous })
	return &got
}

// An expired local Claude Code login is the *normal* state for a member
// who simply isn't using Claude Code at the moment: that token lapses
// within hours and this agent never refreshes it. Popping a modal
// dialog on every scheduled run would therefore nag precisely the
// members who have nothing to act on -- and what it asked them to
// restore (usage-limit percentages for someone consuming no usage)
// carries almost no information. So a 401 here must stay silent.
func TestReportUsageFailureDoesNotPopUpOnExpiredLocalLogin(t *testing.T) {
	notifications := captureNotifications(t)

	var posted usageSnapshotRequest
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&posted)
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()

	app := &appContext{apiEndpoint: server.URL}
	app.reportUsageFailure("tok-1", "secret", 401, nil, 1700000000000)

	if len(*notifications) != 0 {
		t.Fatalf("expected no desktop notification for an expired local login, got %+v", *notifications)
	}

	// Staying silent must not mean staying quiet to the *admin*: the
	// snapshot is still reported so the admin page reflects reality.
	if posted.FetchStatus != "auth_error" {
		t.Fatalf("fetch_status = %q, want \"auth_error\" still reported to bedrock-chat", posted.FetchStatus)
	}
	if posted.FetchErrorMessage == nil || !strings.Contains(*posted.FetchErrorMessage, "401") {
		t.Fatalf("expected the 401 detail to still be reported, got %v", posted.FetchErrorMessage)
	}
}

func TestReportUsageFailureDoesNotPopUpOnOtherErrors(t *testing.T) {
	notifications := captureNotifications(t)

	var posted usageSnapshotRequest
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&posted)
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()

	app := &appContext{apiEndpoint: server.URL}
	app.reportUsageFailure("tok-1", "secret", 503, errServiceUnavailable, 1700000000000)

	if len(*notifications) != 0 {
		t.Fatalf("expected no notification for a transient failure, got %+v", *notifications)
	}
	// A non-401 must never be labelled auth_error: it does not mean the
	// token is invalid, only that usage couldn't be read this once.
	if posted.FetchStatus != "error" {
		t.Fatalf("fetch_status = %q, want \"error\" for a non-401", posted.FetchStatus)
	}
}

var errServiceUnavailable = &simpleError{"service unavailable"}

type simpleError struct{ msg string }

func (e *simpleError) Error() string { return e.msg }

// The chat-side token is the opposite case and must keep its popup: it
// is held server-side only, so the member's machine has no way to
// notice it lapsed, and while it is broken their seat serves no chat at
// all. That is both actionable and invisible otherwise -- exactly what
// a modal dialog is for.
//
// Exercised through notifyChatTokenDisabled, the function run() calls,
// rather than through run() itself: run() ends by calling os.Exit once
// it reaches the (absent) local credentials file, which would take the
// test binary down with it.
func TestChatTokenDisabledStillNotifiesTheMember(t *testing.T) {
	notifications := captureNotifications(t)

	app := &appContext{}
	app.notifyChatTokenDisabled()

	if len(*notifications) != 1 {
		t.Fatalf("expected exactly 1 notification for a disabled chat token, got %d", len(*notifications))
	}
	got := (*notifications)[0]
	if !strings.Contains(strings.ToLower(got.title), "chat token") {
		t.Fatalf("notification title should name the chat token, got %q", got.title)
	}
	// It must tell the member what to actually do, since the whole
	// justification for interrupting them is that the fix is actionable.
	if !strings.Contains(got.message, "claude_teams_member_agent.exe") {
		t.Fatalf("notification should tell the member which exe to run, got %q", got.message)
	}
}

// Guards the split itself: exactly one of the two situations warrants
// interrupting a member. If a future change re-adds a popup to the
// usage path, or drops the one on the chat-token path, this fails.
func TestOnlyTheChatTokenCaseInterruptsTheMember(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()

	notifications := captureNotifications(t)
	app := &appContext{apiEndpoint: server.URL}

	// The routine, expected-to-recur situation: silent.
	app.reportUsageFailure("tok-1", "secret", 401, nil, 1)
	if len(*notifications) != 0 {
		t.Fatalf("the recurring local-login case must not interrupt: %+v", *notifications)
	}

	// The rare, actionable, otherwise-invisible situation: interrupts.
	app.notifyChatTokenDisabled()
	if len(*notifications) != 1 {
		t.Fatalf("the chat-token case must interrupt exactly once, got %d", len(*notifications))
	}
}
