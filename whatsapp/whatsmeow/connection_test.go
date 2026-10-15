package main

import (
	"context"
	"encoding/json"
	"path/filepath"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/types/events"
)

// The Python collector blocks on these (operational.TERMINAL) and only
// retries with backoff on the other connection errors.
var pythonTerminal = map[string]bool{"whatsapp_logged_out": true, "whatsapp_pairing_required": true, "provider_auth_failed": true, "whatsapp_identity_mismatch": true}

func TestNonReconnectingWhatsAppEventsMapToCodesPythonUnderstands(t *testing.T) {
	for _, tc := range []struct {
		event    any
		code     string
		terminal bool
	}{
		{&events.TemporaryBan{Code: events.TempBanSentToTooManyPeople, Expire: time.Hour}, "provider_auth_failed", true},
		{&events.StreamReplaced{}, "provider_auth_failed", true},
		{&events.ClientOutdated{}, "provider_initialization_failed", false},
	} {
		code, detail := connectionFailure(tc.event)
		if code != tc.code || detail == "" || !transportFailures[code] || pythonTerminal[code] != tc.terminal {
			t.Fatalf("%T -> %q %q", tc.event, code, detail)
		}
	}
	for _, other := range []any{&events.Disconnected{}, &events.Connected{}, &events.LoggedOut{}} {
		if code, _ := connectionFailure(other); code != "" {
			t.Fatalf("%T mapped to %q", other, code)
		}
	}
}

func TestConnectFailureIsReportedInsteadOfGenericDisconnect(t *testing.T) {
	p := &Provider{Config: testConfig(t)}
	ctx, cancel := context.WithCancel(context.Background())
	p.cancel = cancel
	go p.failDetail(connectionFailure(&events.TemporaryBan{Expire: time.Hour}))
	if _, err := p.awaitConnected(ctx, make(chan struct{}), time.Minute); err == nil || err.Error() != "provider_auth_failed" {
		t.Fatal("ban during connect not reported as terminal", err)
	}
	raw, err := readPrivate(filepath.Join(p.Config.EventsDirectory, ".transport-failure"), 4096)
	if err != nil {
		t.Fatal(err)
	}
	var failure map[string]any
	if err = json.Unmarshal(raw, &failure); err != nil || failure["code"] != "provider_auth_failed" || failure["detail"] != "whatsapp_temporary_ban" {
		t.Fatal(string(raw), err)
	}
	// Shutdown without a recorded failure keeps the generic code.
	quiet := &Provider{Config: testConfig(t)}
	ctx, cancel = context.WithCancel(context.Background())
	cancel()
	if _, err = quiet.awaitConnected(ctx, make(chan struct{}), time.Minute); err == nil || err.Error() != "provider_disconnected" {
		t.Fatal(err)
	}
}

func TestRequestsAfterFailureReportRecordedConnectionCode(t *testing.T) {
	p := skipProvider(t)
	p.ready.Store(false)
	// Not ready without a failure (e.g. a transient disconnect): unchanged.
	if _, err := p.handle(context.Background(), request{ID: 1, Method: "policy"}, nil); err == nil || err.Error() != "provider_not_ready" {
		t.Fatal(err)
	}
	p.failDetail(connectionFailure(&events.StreamReplaced{}))
	for _, method := range []string{"dialogs", "page", "media", "send_text"} {
		if _, err := p.handle(context.Background(), request{ID: 2, Method: method}, nil); err == nil || err.Error() != "provider_auth_failed" {
			t.Fatal(method, err)
		}
	}
	// A failure code Python does not treat as a connection error stays generic.
	other := skipProvider(t)
	other.fail("policy_expired")
	if _, err := other.handle(context.Background(), request{ID: 3, Method: "dialogs"}, nil); err == nil || err.Error() != "provider_not_ready" {
		t.Fatal(err)
	}
}
