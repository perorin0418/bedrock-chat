//go:build windows

package main

import (
	"syscall"
	"testing"
	"time"
	"unsafe"
)

// TestShowUserNotificationDisplaysAndCanBeDismissed exercises the real
// MessageBoxW code path in showUserNotification end-to-end: it starts
// the notification (which blocks the calling goroutine until
// dismissed, exactly as it will in production), waits for the actual
// window to appear on screen by title, then closes it the same way a
// member clicking OK would (WM_CLOSE), and asserts showUserNotification
// actually returns. This is a real integration check of the Windows
// API calls, not a mock -- if MessageBoxW's arguments were wrong (bad
// pointer, wrong flags) this would hang the test until Go's test
// timeout instead of passing.
func TestShowUserNotificationDisplaysAndCanBeDismissed(t *testing.T) {
	const title = "jcode-test-notification-dismiss-check"
	done := make(chan struct{})

	go func() {
		showUserNotification(title, "Automated test message -- this window is closed programmatically, not by a human.")
		close(done)
	}()

	user32 := syscall.NewLazyDLL("user32.dll")
	procFindWindowW := user32.NewProc("FindWindowW")
	procPostMessageW := user32.NewProc("PostMessageW")
	titlePtr, err := syscall.UTF16PtrFromString(title)
	if err != nil {
		t.Fatalf("UTF16PtrFromString: %v", err)
	}

	var hwnd uintptr
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		hwnd, _, _ = procFindWindowW.Call(0, uintptr(unsafe.Pointer(titlePtr)))
		if hwnd != 0 {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	if hwnd == 0 {
		t.Fatal("MessageBoxW window never appeared within 10s -- FindWindowW could not locate it by title")
	}
	t.Logf("found MessageBoxW window, hwnd=%d", hwnd)

	const wmClose = 0x0010
	ret, _, _ := procPostMessageW.Call(hwnd, wmClose, 0, 0)
	if ret == 0 {
		t.Fatal("PostMessageW(WM_CLOSE) returned 0 (failed)")
	}

	select {
	case <-done:
		// showUserNotification returned after the simulated dismiss --
		// confirms MessageBoxW actually unblocks on WM_CLOSE and the
		// function returns control normally, matching how a real
		// member clicking OK (or the 50-minute auto-dismiss timer)
		// behaves in production.
	case <-time.After(10 * time.Second):
		t.Fatal("showUserNotification did not return within 10s after WM_CLOSE was posted")
	}
}
