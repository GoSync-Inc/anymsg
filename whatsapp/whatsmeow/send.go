package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"math"
	"regexp"
	"strings"
	"time"
	"unicode/utf16"
	"unicode/utf8"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
)

// This small dependency boundary permits tests of the actual policy/receipt
// contract without creating a client, authorizing a device or sending anything.
type sendDependencies struct {
	policy func() (Policy, error)
	get    func(string, string) (*events.Message, error)
	put    func(*events.Message, string) error
	send   func(context.Context, types.JID, *waE2E.Message, whatsmeow.SendRequestExtra) (whatsmeow.SendResponse, error)
	newID  func() string
	owner  types.JID
}

func (p *Provider) sendDependencies() sendDependencies {
	owner, _ := ParseChatID(p.Config.ExpectedWID)
	return sendDependencies{policy: p.policy, get: p.Capture.Get, put: p.Capture.Put, owner: owner,
		newID: func() string { return p.Client.GenerateMessageID() },
		send: func(ctx context.Context, to types.JID, message *waE2E.Message, extra whatsmeow.SendRequestExtra) (whatsmeow.SendResponse, error) {
			return p.Client.SendMessage(ctx, to, message, extra)
		}}
}

type textRequest struct {
	Chat            string  `json:"chat"`
	Text            string  `json:"text"`
	ExpiresAt       float64 `json:"expires_at"`
	ReplyProviderID string  `json:"reply_provider_id,omitempty"`
	LinkPreview     *bool   `json:"link_preview,omitempty"`
}

func decodeStrict(raw json.RawMessage, value any) error {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(value); err != nil {
		return err
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return errors.New("invalid_json")
	}
	return nil
}

func safeText(value string, maximum int, empty bool) bool {
	if !utf8.ValidString(value) || len(utf16.Encode([]rune(value))) > maximum || (!empty && strings.TrimSpace(value) == "") {
		return false
	}
	for _, r := range value {
		if (r < 32 && r != '\n' && r != '\r' && r != '\t') || r == 127 {
			return false
		}
	}
	return true
}

var serializedMessagePattern = regexp.MustCompile(`^[a-zA-Z0-9_.:@-]{1,256}$`)

func validMessageReference(chat, id string) bool {
	return serializedMessagePattern.MatchString(id) &&
		((strings.HasPrefix(id, "true_"+chat+"_") && len(id) > len("true_"+chat+"_")) ||
			(strings.HasPrefix(id, "false_"+chat+"_") && len(id) > len("false_"+chat+"_")))
}

func exactCaptured(d sendDependencies, chat, id string) (*events.Message, error) {
	if !validMessageReference(chat, id) {
		return nil, errors.New("operation_target_invalid")
	}
	m, err := d.get(chat, id)
	if err != nil || m == nil {
		return nil, errors.New("operation_target_unavailable")
	}
	if LegacyChatID(m.Info.Chat) != chat || LegacyMessageID(m) != id || m.Message == nil ||
		(m.Message.GetProtocolMessage() != nil && m.Message.GetProtocolMessage().GetType() == waE2E.ProtocolMessage_REVOKE) {
		return nil, errors.New("operation_target_invalid")
	}
	return m, nil
}

func messageKey(m *events.Message) *waE2E.ContextInfo {
	return &waE2E.ContextInfo{StanzaID: proto.String(m.Info.ID),
		Participant:   proto.String(m.Info.Sender.ToNonAD().String()),
		QuotedMessage: proto.Clone(m.Message).(*waE2E.Message)}
}

func (p *Provider) SendText(ctx context.Context, raw json.RawMessage) (any, error) {
	var req textRequest
	if decodeStrict(raw, &req) != nil {
		return nil, errors.New("send_target_invalid")
	}
	return sendText(ctx, req, p.sendDependencies())
}

func sendText(ctx context.Context, req textRequest, d sendDependencies) (any, error) {
	check := func() error {
		policy, err := d.policy()
		if err != nil || !policy.Enabled || !policy.SendEnabled {
			return errors.New("sending_disabled")
		}
		if _, err = ParseChatID(req.Chat); err != nil || !policy.Allows(req.Chat) || !safeText(req.Text, 4000, false) {
			return errors.New("send_target_invalid")
		}
		if math.IsNaN(req.ExpiresAt) || math.IsInf(req.ExpiresAt, 0) || req.ExpiresAt <= float64(time.Now().UnixNano())/1e9 {
			return errors.New("send_expired")
		}
		return nil
	}
	if err := check(); err != nil {
		return nil, err
	}
	// Native WhatsApp does not fetch arbitrary links. Enabling rich previews needs
	// a separate bounded URL-fetch contract; do not silently claim it happened.
	if req.LinkPreview != nil && *req.LinkPreview {
		return nil, errors.New("link_preview_unsupported")
	}
	to, _ := ParseChatID(req.Chat)
	message := &waE2E.Message{Conversation: proto.String(req.Text)}
	if req.ReplyProviderID != "" {
		quoted, err := exactCaptured(d, req.Chat, req.ReplyProviderID)
		if err != nil {
			return nil, errors.New("send_target_invalid")
		}
		message = &waE2E.Message{ExtendedTextMessage: &waE2E.ExtendedTextMessage{Text: proto.String(req.Text), ContextInfo: messageKey(quoted)}}
	}
	if err := check(); err != nil {
		return nil, err
	}
	id, err := sendAndCapture(ctx, d, to, message)
	if err != nil {
		return nil, err
	}
	return map[string]any{"provider_message_id": id}, nil
}

// A server acknowledgement is evidence of acceptance, not delivery or reading.
// Every error after invoking send is ambiguous and must never trigger a retry.
func sendAndCapture(ctx context.Context, d sendDependencies, to types.JID, message *waE2E.Message) (string, error) {
	id := d.newID()
	if id == "" || !serializedMessagePattern.MatchString(id) {
		return "", errors.New("send_target_invalid")
	}
	resp, err := d.send(ctx, to, message, whatsmeow.SendRequestExtra{ID: id})
	if err != nil {
		return "", errors.New("provider_operation_uncertain")
	}
	if resp.ID != id || resp.Timestamp.IsZero() || (!resp.Chat.IsEmpty() && resp.Chat.ToNonAD() != to.ToNonAD()) {
		return "", errors.New("send_receipt_mismatch")
	}
	event := &events.Message{Info: types.MessageInfo{MessageSource: types.MessageSource{Chat: to, Sender: d.owner, IsFromMe: true, IsGroup: to.Server == types.GroupServer}, ID: id, Timestamp: resp.Timestamp}, Message: message, RawMessage: message}
	if err = d.put(event, "live"); err != nil {
		return "", errors.New("provider_operation_uncertain")
	}
	return LegacyMessageID(event), nil
}
