package main

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
)

const operationsChat = "15551234567@c.us"
const operationsGroup = "15550000000-123456789@g.us"

func operationMessage(chat, id string, fromMe bool, stamp int64) *events.Message {
	j, _ := ParseChatID(chat)
	sender, _ := ParseChatID("15559876543@c.us")
	if fromMe {
		sender, _ = ParseChatID("15550000000@c.us")
	}
	wire := &waE2E.Message{Conversation: proto.String("original text")}
	return &events.Message{Info: types.MessageInfo{MessageSource: types.MessageSource{Chat: j, Sender: sender, IsFromMe: fromMe, IsGroup: j.Server == types.GroupServer}, ID: id, Timestamp: time.Unix(stamp, 0)}, Message: wire, RawMessage: wire}
}

func allowedOperationPolicy() Policy {
	ops := []string{}
	for op := range operationFields {
		ops = append(ops, op)
	}
	return Policy{Enabled: true, SendEnabled: true, DiscoverChats: true, OperationsEnabled: ops}
}

func sendFake(t *testing.T) sendDependencies {
	t.Helper()
	owner, _ := ParseChatID("15550000000@c.us")
	return sendDependencies{
		owner: owner, policy: func() (Policy, error) { return allowedOperationPolicy(), nil },
		get:   func(chat, id string) (*events.Message, error) { return nil, errors.New("missing") },
		put:   func(m *events.Message, origin string) error { return nil },
		newID: func() string { return "TESTSTANZA" },
		send: func(_ context.Context, to types.JID, _ *waE2E.Message, extra whatsmeow.SendRequestExtra) (whatsmeow.SendResponse, error) {
			return whatsmeow.SendResponse{ID: extra.ID, Timestamp: time.Unix(1700000000, 0), Chat: to, Sender: owner}, nil
		},
	}
}

func textFixture() textRequest {
	return textRequest{Chat: operationsChat, Text: "*bold* _italic_ https://example.org", ExpiresAt: float64(time.Now().Add(time.Hour).Unix())}
}
func requireError(t *testing.T, err error, code string) {
	t.Helper()
	if err == nil || err.Error() != code {
		t.Fatalf("want %s, got %v", code, err)
	}
}

func TestNativeTextValidatesBeforeSend(t *testing.T) {
	for name, modify := range map[string]func(*textRequest){
		"expired": func(r *textRequest) { r.ExpiresAt = 1 }, "nan": func(r *textRequest) { r.ExpiresAt = math.NaN() },
		"infinite": func(r *textRequest) { r.ExpiresAt = math.Inf(1) }, "empty": func(r *textRequest) { r.Text = " \n" },
		"control": func(r *textRequest) { r.Text = "unsafe\x00text" }, "utf16": func(r *textRequest) { r.Text = strings.Repeat("😀", 2001) },
		"target": func(r *textRequest) { r.Chat = "15551234567@evil" }, "rich_preview": func(r *textRequest) { v := true; r.LinkPreview = &v },
	} {
		t.Run(name, func(t *testing.T) {
			d := sendFake(t)
			called := false
			d.send = func(context.Context, types.JID, *waE2E.Message, whatsmeow.SendRequestExtra) (whatsmeow.SendResponse, error) {
				called = true
				return whatsmeow.SendResponse{}, nil
			}
			r := textFixture()
			modify(&r)
			_, err := sendText(context.Background(), r, d)
			if err == nil || called {
				t.Fatalf("invalid request reached provider: %v, called=%v", err, called)
			}
		})
	}
}

func TestNativeTextPlainLinkAndQuoteAreExact(t *testing.T) {
	d := sendFake(t)
	r := textFixture()
	f := false
	r.LinkPreview = &f
	quoted := operationMessage(operationsChat, "QUOTED", false, 1600000000)
	r.ReplyProviderID = LegacyMessageID(quoted)
	d.get = func(chat, id string) (*events.Message, error) { return quoted, nil }
	d.send = func(_ context.Context, to types.JID, wire *waE2E.Message, extra whatsmeow.SendRequestExtra) (whatsmeow.SendResponse, error) {
		if wire.GetExtendedTextMessage().GetText() != r.Text || wire.GetExtendedTextMessage().GetContextInfo().GetStanzaID() != "QUOTED" || wire.GetExtendedTextMessage().GetMatchedText() != "" {
			t.Fatal("plain formatting/quote/preview changed")
		}
		return whatsmeow.SendResponse{ID: extra.ID, Timestamp: time.Unix(1700000000, 0), Chat: to, Sender: d.owner}, nil
	}
	d.put = func(m *events.Message, origin string) error {
		if m.Info.Timestamp.Unix() != 1700000000 || origin != "live" || !m.Info.IsFromMe {
			t.Fatal("capture invented receipt evidence")
		}
		return nil
	}
	result, err := sendText(context.Background(), r, d)
	if err != nil {
		t.Fatal(err)
	}
	if result.(map[string]any)["provider_message_id"] != "true_"+operationsChat+"_TESTSTANZA" {
		t.Fatalf("unexpected receipt %v", result)
	}
	quoted.Info.Chat, _ = ParseChatID("15559999999@c.us")
	_, err = sendText(context.Background(), r, d)
	requireError(t, err, "send_target_invalid")
}

func TestNativeTextChecksRevocationAfterResolvingReply(t *testing.T) {
	d := sendFake(t)
	r := textFixture()
	quoted := operationMessage(operationsChat, "Q", false, 1)
	r.ReplyProviderID = LegacyMessageID(quoted)
	p := allowedOperationPolicy()
	d.policy = func() (Policy, error) { return p, nil }
	d.get = func(string, string) (*events.Message, error) { p.SendEnabled = false; return quoted, nil }
	d.send = func(context.Context, types.JID, *waE2E.Message, whatsmeow.SendRequestExtra) (whatsmeow.SendResponse, error) {
		t.Fatal("revoked send reached provider")
		return whatsmeow.SendResponse{}, nil
	}
	_, err := sendText(context.Background(), r, d)
	requireError(t, err, "sending_disabled")
}

func TestNativeSendNeverRetriesAmbiguousOutcome(t *testing.T) {
	for _, mode := range []string{"timeout", "wrong_id", "wrong_chat", "missing_timestamp", "capture_failure"} {
		t.Run(mode, func(t *testing.T) {
			d := sendFake(t)
			calls, puts := 0, 0
			d.send = func(_ context.Context, to types.JID, _ *waE2E.Message, extra whatsmeow.SendRequestExtra) (whatsmeow.SendResponse, error) {
				calls++
				response := whatsmeow.SendResponse{ID: extra.ID, Chat: to, Sender: d.owner, Timestamp: time.Unix(1700000000, 0)}
				switch mode {
				case "timeout":
					return response, context.DeadlineExceeded
				case "wrong_id":
					response.ID = "OTHER"
				case "wrong_chat":
					response.Chat, _ = ParseChatID("15559999999@c.us")
				case "missing_timestamp":
					response.Timestamp = time.Time{}
				}
				return response, nil
			}
			d.put = func(*events.Message, string) error { puts++; return errors.New("disk failure") }
			_, err := sendText(context.Background(), textFixture(), d)
			want := "send_receipt_mismatch"
			if mode == "timeout" || mode == "capture_failure" {
				want = "provider_operation_uncertain"
			}
			requireError(t, err, want)
			if calls != 1 || (mode != "capture_failure" && puts != 0) {
				t.Fatalf("calls=%d puts=%d", calls, puts)
			}
		})
	}
}

func TestNativeTextProtocolRejectsUnknownFieldsAndTrailingJSON(t *testing.T) {
	for _, raw := range []string{`{"chat":"x","text":"y","expires_at":1,"raw_api":"danger"}`, `{} {}`, `{"text":42}`} {
		var r textRequest
		if decodeStrict(json.RawMessage(raw), &r) == nil {
			t.Fatalf("accepted %s", raw)
		}
	}
}

func TestNativeSendPNReceiptRetainsVerifiedWireIdentity(t *testing.T) {
	d := sendFake(t)
	requested, _ := ParseChatID(operationsChat)
	wireChat := types.NewJID("999900001111", types.HiddenUserServer)
	d.ownerLID = types.NewJID("999900002222", types.HiddenUserServer)
	d.lidForPN = func(_ context.Context, pn types.JID) (types.JID, error) {
		if pn != requested {
			t.Fatal("unexpected identity lookup", pn)
		}
		return wireChat, nil
	}
	d.pnForLID = func(_ context.Context, lid types.JID) (types.JID, error) {
		if lid != wireChat {
			t.Fatal("unexpected identity lookup", lid)
		}
		return requested, nil
	}
	calls := 0
	d.send = func(_ context.Context, to types.JID, wire *waE2E.Message, extra whatsmeow.SendRequestExtra) (whatsmeow.SendResponse, error) {
		calls++
		if to != requested {
			t.Fatal("requested destination changed")
		}
		return whatsmeow.SendResponse{ID: extra.ID, Chat: wireChat, Sender: d.ownerLID, Timestamp: time.Unix(1700000000, 0)}, nil
	}
	var captured *events.Message
	d.put = func(m *events.Message, _ string) error { captured = m; return nil }
	result, err := sendText(context.Background(), textFixture(), d)
	if err != nil {
		t.Fatal("verified PN to LID receipt rejected:", err)
	}
	if calls != 1 || captured == nil {
		t.Fatal("send/capture count", calls, captured)
	}
	if captured.Info.Chat != wireChat || captured.Info.Sender != d.ownerLID || captured.Info.RecipientAlt != requested || captured.Info.SenderAlt != d.owner {
		t.Fatal("capture lost actual wire/alternate identities", captured.Info.MessageSource)
	}
	if got := result.(map[string]any)["provider_message_id"]; got != "true_"+operationsChat+"_TESTSTANZA" {
		t.Fatal("public requested target ID changed", got)
	}
	ref, ok := result.(map[string]any)["archive_reference"].(map[string]any)
	if !ok || ref["provider_chat_id"] != "999900001111@lid" || ref["provider_message_id"] != LegacyMessageID(captured) {
		t.Fatal("missing exact wire archive reference", result)
	}
	if LegacyMessageID(captured) != "true_999900001111@lid_TESTSTANZA" {
		t.Fatal("wire archive namespace rewritten")
	}
}

func TestNativeSendGroupReceiptPreservesPublicIDAndWireSender(t *testing.T) {
	d := sendFake(t)
	d.ownerLID = types.NewJID("999900002222", types.HiddenUserServer)
	r := textFixture()
	r.Chat = operationsGroup
	d.lidForPN = func(context.Context, types.JID) (types.JID, error) {
		t.Fatal("group alias lookup")
		return types.EmptyJID, nil
	}
	d.send = func(_ context.Context, to types.JID, _ *waE2E.Message, extra whatsmeow.SendRequestExtra) (whatsmeow.SendResponse, error) {
		return whatsmeow.SendResponse{ID: extra.ID, Chat: to, Sender: d.ownerLID, Timestamp: time.Unix(1700000000, 0)}, nil
	}
	var captured *events.Message
	d.put = func(m *events.Message, _ string) error { captured = m; return nil }
	result, err := sendText(context.Background(), r, d)
	if err != nil {
		t.Fatal(err)
	}
	got := result.(map[string]any)
	if got["provider_message_id"] != "true_"+operationsGroup+"_TESTSTANZA_"+LegacyChatID(d.owner) {
		t.Fatal("public group receipt changed", got)
	}
	ref, ok := got["archive_reference"].(map[string]any)
	if !ok || ref["provider_chat_id"] != operationsGroup || ref["provider_message_id"] != "true_"+operationsGroup+"_TESTSTANZA_"+LegacyChatID(d.ownerLID) ||
		captured.Info.Sender != d.ownerLID || captured.Info.SenderAlt != d.owner {
		t.Fatal("wire group sender evidence lost", got, captured)
	}
}

func TestNativeSendRejectsUnprovenReceiptIdentitiesOnce(t *testing.T) {
	for _, mode := range []string{"no_mapping", "different_lid", "reverse_mismatch", "mapping_error", "same_digits", "group_alias", "lid_to_pn", "unknown_sender", "missing_sender", "missing_chat"} {
		t.Run(mode, func(t *testing.T) {
			d := sendFake(t)
			requested, _ := ParseChatID(operationsChat)
			wireChat := types.NewJID("999900001111", types.HiddenUserServer)
			d.ownerLID = types.NewJID("999900002222", types.HiddenUserServer)
			d.lidForPN = func(context.Context, types.JID) (types.JID, error) { return wireChat, nil }
			d.pnForLID = func(context.Context, types.JID) (types.JID, error) { return requested, nil }
			r := textFixture()
			sender := d.ownerLID
			switch mode {
			case "no_mapping":
				d.lidForPN = nil
			case "different_lid":
				d.lidForPN = func(context.Context, types.JID) (types.JID, error) {
					return types.NewJID("999900003333", types.HiddenUserServer), nil
				}
			case "reverse_mismatch":
				d.pnForLID = func(context.Context, types.JID) (types.JID, error) { return d.owner, nil }
			case "mapping_error":
				d.pnForLID = func(context.Context, types.JID) (types.JID, error) { return types.EmptyJID, errors.New("read failure") }
			case "same_digits":
				wireChat = types.NewJID(requested.User, types.HiddenUserServer)
				d.lidForPN = nil
			case "group_alias":
				r.Chat = operationsGroup
			case "lid_to_pn":
				r.Chat = LegacyChatID(wireChat)
				wireChat = requested
			case "unknown_sender":
				sender = types.NewJID("999900009999", types.HiddenUserServer)
				wireChat = requested
			case "missing_sender":
				sender = types.EmptyJID
				wireChat = requested
			case "missing_chat":
				wireChat = types.EmptyJID
			}
			calls, puts := 0, 0
			d.send = func(_ context.Context, _ types.JID, _ *waE2E.Message, extra whatsmeow.SendRequestExtra) (whatsmeow.SendResponse, error) {
				calls++
				return whatsmeow.SendResponse{ID: extra.ID, Chat: wireChat, Sender: sender, Timestamp: time.Unix(1700000000, 0)}, nil
			}
			d.put = func(*events.Message, string) error { puts++; return nil }
			_, err := sendText(context.Background(), r, d)
			requireError(t, err, "send_receipt_mismatch")
			if calls != 1 || puts != 0 {
				t.Fatalf("ambiguous send retried/captured: calls=%d puts=%d", calls, puts)
			}
		})
	}
}

func TestNativeSendWireEchoKeepsOneArchiveIdentity(t *testing.T) {
	d := sendFake(t)
	s := openTestStore(t)
	s.Owner = d.owner
	s.OwnerLID = types.NewJID("999900002222", types.HiddenUserServer)
	d.ownerLID = s.OwnerLID
	requested, _ := ParseChatID(operationsChat)
	wireChat := types.NewJID("999900001111", types.HiddenUserServer)
	d.lidForPN = func(context.Context, types.JID) (types.JID, error) { return wireChat, nil }
	d.pnForLID = func(context.Context, types.JID) (types.JID, error) { return requested, nil }
	d.put = s.Put
	d.send = func(_ context.Context, _ types.JID, _ *waE2E.Message, extra whatsmeow.SendRequestExtra) (whatsmeow.SendResponse, error) {
		return whatsmeow.SendResponse{ID: extra.ID, Chat: wireChat, Sender: d.ownerLID, Timestamp: time.Unix(1700000000, 0)}, nil
	}
	if _, err := sendText(context.Background(), textFixture(), d); err != nil {
		t.Fatal(err)
	}
	// Another own device can enrich the raw observation; its namespace and stanza
	// still identify the same archive message rather than a second PN copy.
	echo := operationMessage(LegacyChatID(wireChat), "TESTSTANZA", true, 1700000000)
	echo.Info.Sender = d.ownerLID
	echo.Info.Sender.Device = 2
	echo.Info.SenderAlt = d.owner
	echo.Info.RecipientAlt = requested
	echo.Message = &waE2E.Message{Conversation: proto.String(textFixture().Text)}
	echo.RawMessage = echo.Message
	if err := s.Put(echo, "live"); err != nil {
		t.Fatal(err)
	}
	rows, err := s.Page(LegacyChatID(wireChat), 100, nil)
	if err != nil || len(rows) != 1 {
		t.Fatalf("wire echo duplicated: %v rows=%d", err, len(rows))
	}
	rows, err = s.Page(operationsChat, 100, nil)
	if err != nil || len(rows) != 0 {
		t.Fatalf("invented PN archive copy: %v rows=%d", err, len(rows))
	}
}
