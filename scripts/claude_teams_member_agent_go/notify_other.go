//go:build !windows

package main

// This agent ships as a Windows .exe (see main.go's package comment);
// the real showUserNotification is a MessageBoxW call in
// notify_windows.go. This non-Windows stub exists purely so the
// package still builds -- and therefore `go test ./...` still runs --
// on the Linux/macOS machines this repo is developed and CI'd on.
// Without it, a Linux `go test` fails at link time with "undefined:
// showUserNotification" and none of the platform-independent logic
// (CLI resolution, config parsing, API shapes) can be exercised at
// all. There is no message box to show off-Windows, so the message is
// simply written to the same console stream every other status line
// uses.
func showUserNotification(title, message string) {
	infof("[%s] %s", title, message)
}
