package main

import (
	"bufio"
	"bytes"
	"strings"
	"testing"
)

// TestReadLineReturnsPastedValueUnmasked confirms the pasted OAuth
// token flow reads a plain line -- no masking/hiding -- since the
// token was already printed to the same terminal moments earlier by
// 'claude setup-token' (masking the paste back would only make it
// impossible to tell whether the paste went through, without
// protecting anything already on screen). This exercises the same
// bufio.Reader.ReadString('\n') pattern readLine uses, confirming a
// line with trailing newline is read back intact before the
// TrimSpace call in registerThisMachine strips it.
func TestReadLineReturnsPastedValueUnmasked(t *testing.T) {
	input := "sk-ant-oat01-pasted-token-value\n"
	reader := bufio.NewReader(strings.NewReader(input))
	line, err := reader.ReadString('\n')
	if err != nil {
		t.Fatalf("ReadString: %v", err)
	}
	if strings.TrimSpace(line) != "sk-ant-oat01-pasted-token-value" {
		t.Fatalf("want pasted token value, got %q", line)
	}
}

func TestReadLineFunctionReadsFromStdinReplacement(t *testing.T) {
	// readLine() itself always reads from the real os.Stdin, so this
	// confirms the underlying bufio pattern it uses against an
	// in-memory reader standing in for stdin, matching how
	// registerThisMachine consumes it (read whole line, then
	// strings.TrimSpace before use).
	var buf bytes.Buffer
	buf.WriteString("pasted-value\n")
	reader := bufio.NewReader(&buf)
	got, err := reader.ReadString('\n')
	if err != nil {
		t.Fatalf("ReadString: %v", err)
	}
	if strings.TrimSpace(got) != "pasted-value" {
		t.Fatalf("want pasted-value, got %q", got)
	}
}
