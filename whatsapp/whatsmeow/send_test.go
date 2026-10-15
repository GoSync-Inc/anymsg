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
			return whatsmeow.SendResponse{ID: extra.ID, Timestamp: time.Unix(1700000000, 0), Chat: to}, nil
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
		return whatsmeow.SendResponse{ID: extra.ID, Timestamp: time.Unix(1700000000, 0), Chat: to}, nil
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
				response := whatsmeow.SendResponse{ID: extra.ID, Chat: to, Timestamp: time.Unix(1700000000, 0)}
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
