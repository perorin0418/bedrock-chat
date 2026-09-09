//go:build windows

package main

import (
	"syscall"
	"time"
	"unsafe"
)

// showUserNotification is the direct equivalent of the .ps1
// predecessor's Show-UserNotification. That version used
// WScript.Shell's COM Popup because PowerShell had no built-in
// alternative without an extra module; this Go rewrite calls
// user32.dll's MessageBoxW directly via syscall, which needs no COM,
// no extra module, and no external process at all.
//
// Design carried over unchanged from the .ps1 version (see its
// comments, preserved here): this runs unattended from Task Scheduler
// most of the time, so there's no guarantee anyone is at the keyboard
// to see console output. A balloon/toast notification is easy to miss
// or dismiss without reading, so this uses an actual modal dialog that
// stays on top until acknowledged. Never lets a notification failure
// (e.g. no desktop session, some locked-down environment) break the
// rest of the program -- this is purely informational, so failures are
// only warned about, never fatal.
//
// One behavior difference from the .ps1 version's WScript.Shell.Popup:
// MessageBoxW has no built-in auto-dismiss timeout. The .ps1 version
// used a 3000s (50min) timeout so an unattended run nobody is at the
// keyboard for doesn't leave a dialog open forever, piling up against
// the next scheduled run. This is reproduced here with a dedicated
// goroutine that finds the dialog's window and closes it via
// EndDialog/WM_CLOSE after the same timeout, since MessageBoxW itself
// blocks the calling thread until dismissed either way.
func showUserNotification(title, message string) {
	defer func() {
		// A notification failure must never break the rest of the
		// program -- mirrors the .ps1 predecessor's try/catch here.
		if r := recover(); r != nil {
			warnf("(could not show a desktop notification: %v)", r)
		}
	}()

	user32 := syscall.NewLazyDLL("user32.dll")
	procMessageBoxW := user32.NewProc("MessageBoxW")
	procFindWindowW := user32.NewProc("FindWindowW")
	procPostMessageW := user32.NewProc("PostMessageW")

	titlePtr, err := syscall.UTF16PtrFromString(title)
	if err != nil {
		warnf("(could not show a desktop notification: %v)", err)
		return
	}
	messagePtr, err := syscall.UTF16PtrFromString(message)
	if err != nil {
		warnf("(could not show a desktop notification: %v)", err)
		return
	}

	const (
		mbOKOnly           = 0x00000000
		mbIconExclamation  = 0x00000030
		mbSystemModal      = 0x00001000
		mbSetForeground    = 0x00010000
		wmClose            = 0x0010
		autoDismissSeconds = 3000
	)

	done := make(chan struct{})
	go func() {
		// Poll for the dialog window by its exact title and close it
		// once the timeout elapses, since MessageBoxW blocks the
		// calling goroutine until the user (or this closer) dismisses
		// it. Polling (rather than a single timer) also lets this
		// notice immediately if the dialog was already dismissed
		// (the `done` channel closes right after MessageBoxW returns).
		deadline := time.Now().Add(autoDismissSeconds * time.Second)
		ticker := time.NewTicker(500 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-done:
				return
			case <-ticker.C:
				if time.Now().After(deadline) {
					hwnd, _, _ := procFindWindowW.Call(0, uintptr(unsafe.Pointer(titlePtr)))
					if hwnd != 0 {
						procPostMessageW.Call(hwnd, wmClose, 0, 0)
					}
					return
				}
			}
		}
	}()
	defer close(done)

	procMessageBoxW.Call(
		0,
		uintptr(unsafe.Pointer(messagePtr)),
		uintptr(unsafe.Pointer(titlePtr)),
		uintptr(mbOKOnly|mbIconExclamation|mbSystemModal|mbSetForeground),
	)
}
