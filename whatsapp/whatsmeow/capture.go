package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	waE2E "go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
	"os"
	"strings"
	"time"
)

// Small deterministic offline bridge lets the existing Python validator check
// real native envelopes. It cannot initialize or touch a WhatsApp session.
func normalizeFixture(path string) error {
	var v struct {
		Synthetic                                                            bool `json:"synthetic"`
		Account, Chat, Sender, Owner, ID, Text, Kind, Filename, Mime, Origin string
		OwnerLID                                                             string `json:"owner_lid"`
		Timestamp                                                            int64
		FromMe                                                               bool `json:"from_me"`
		Bytes                                                                uint64
		Duration                                                             uint32 `json:"duration_seconds"`
		ReplyID                                                              string `json:"reply_to_id"`
		ReplyParticipant                                                     string `json:"reply_participant"`
		Mentions                                                             []string
		SenderAlt                                                            string `json:"sender_alt"`
		RecipientAlt                                                         string `json:"recipient_alt"`
	}
	data, err := readPrivate(path, 1<<20)
	if err != nil {
		return err
	}
	if json.Unmarshal(data, &v) != nil || !v.Synthetic {
		return errors.New("invalid_fixture")
	}
	chat, err := ParseChatID(v.Chat)
	if err != nil {
		return err
	}
	sender, err := ParseChatID(v.Sender)
	if err != nil && v.Sender != "" {
		return err
	}
	owner, err := ParseChatID(v.Owner)
	if err != nil {
		return err
	}
	lid := types.EmptyJID
	if v.OwnerLID != "" {
		lid, err = ParseChatID(v.OwnerLID)
		if err != nil {
			return err
		}
	}
	msg := &waE2E.Message{Conversation: proto.String(v.Text)}
	ctx := &waE2E.ContextInfo{StanzaID: proto.String(v.ReplyID), Participant: proto.String(v.ReplyParticipant), MentionedJID: v.Mentions}
	switch v.Kind {
	case "image":
		msg = &waE2E.Message{ImageMessage: &waE2E.ImageMessage{Caption: proto.String(v.Text), Mimetype: proto.String(v.Mime), FileLength: proto.Uint64(v.Bytes), ContextInfo: ctx}}
	case "ptt":
		msg = &waE2E.Message{AudioMessage: &waE2E.AudioMessage{PTT: proto.Bool(true), Seconds: proto.Uint32(v.Duration), Mimetype: proto.String(v.Mime), FileLength: proto.Uint64(v.Bytes), ContextInfo: ctx}}
	case "document":
		msg = &waE2E.Message{DocumentMessage: &waE2E.DocumentMessage{FileName: proto.String(v.Filename), Caption: proto.String(v.Text), Mimetype: proto.String(v.Mime), FileLength: proto.Uint64(v.Bytes), ContextInfo: ctx}}
	case "notification":
		msg = nil
	default:
		msg = &waE2E.Message{ExtendedTextMessage: &waE2E.ExtendedTextMessage{Text: proto.String(v.Text), ContextInfo: ctx}}
	}
	event := &events.Message{Info: types.MessageInfo{MessageSource: types.MessageSource{Chat: chat, Sender: sender, IsGroup: chat.Server == types.GroupServer, IsFromMe: v.FromMe}, ID: v.ID, Timestamp: time.Unix(v.Timestamp, 0)}, Message: msg, RawMessage: msg}
	if v.SenderAlt != "" {
		event.Info.SenderAlt, _ = ParseChatID(v.SenderAlt)
	}
	if v.RecipientAlt != "" {
		event.Info.RecipientAlt, _ = ParseChatID(v.RecipientAlt)
	}
	if v.Account == "" {
		v.Account = "test"
	}
	envelope, err := captureEnvelope(v.Account, event, owner, fixtureOrigin(v.Origin), lid)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(envelope)
}

// IDs deliberately retain the observed namespace. PN/LID mappings are separate
// evidence and never silently rewrite existing archive references.
func LegacyMessageID(e *events.Message) string {
	id := fmt.Sprintf("%t_%s_%s", e.Info.IsFromMe, LegacyChatID(e.Info.Chat), e.Info.ID)
	if e.Info.IsGroup && !e.Info.Sender.IsEmpty() {
		id += "_" + LegacyChatID(e.Info.Sender)
	}
	return id
}
func unwrap(e *events.Message) *events.Message {
	copy := *e
	m := e.RawMessage
	if m == nil {
		m = e.Message
	}
	if m != nil {
		copy.RawMessage = proto.Clone(m).(*waE2E.Message)
		copy.UnwrapRaw()
	}
	return &copy
}
func messageContext(m *waE2E.Message) *waE2E.ContextInfo {
	switch {
	case m.GetExtendedTextMessage() != nil:
		return m.GetExtendedTextMessage().GetContextInfo()
	case m.GetImageMessage() != nil:
		return m.GetImageMessage().GetContextInfo()
	case m.GetVideoMessage() != nil:
		return m.GetVideoMessage().GetContextInfo()
	case m.GetAudioMessage() != nil:
		return m.GetAudioMessage().GetContextInfo()
	case m.GetDocumentMessage() != nil:
		return m.GetDocumentMessage().GetContextInfo()
	case m.GetStickerMessage() != nil:
		return m.GetStickerMessage().GetContextInfo()
	}
	return nil
}
func observedPair(id, other types.JID, at string) any {
	id = id.ToNonAD()
	other = other.ToNonAD()
	pn, lid := id, other
	if pn.Server == types.HiddenUserServer {
		pn, lid = other, id
	}
	if pn.Server != types.DefaultUserServer || lid.Server != types.HiddenUserServer || pn.User == "" || lid.User == "" {
		return nil
	}
	return map[string]any{"identity_id": LegacyChatID(id), "pn": LegacyChatID(pn), "lid": LegacyChatID(lid), "provenance": "whatsmeow_message_alt_v1", "observed_at": at}
}
func NormalizeMessage(e *events.Message, owner types.JID, ownerAliases ...types.JID) (map[string]any, error) {
	if e == nil || e.Info.ID == "" || len(e.Info.ID) > 120 || e.Info.Timestamp.Unix() <= 0 {
		return nil, errors.New("invalid_provider_message")
	}
	chat := LegacyChatID(e.Info.Chat)
	if !chatPattern.MatchString(chat) {
		return nil, errors.New("unsupported_chat")
	}
	e = unwrap(e)
	m := e.Message
	body, kind, media := "", "unknown", ""
	var filename, mime, size, duration, reply, mentions, replyOwner, forwarded, edited any
	if e.IsEdit && e.Info.MsgBotInfo.EditSenderTimestampMS.UnixMilli() > 0 {
		edited = e.Info.MsgBotInfo.EditSenderTimestampMS.UnixMilli()
	}
	service := false
	if m == nil {
		service = true
		kind = "notification"
	} else {
		body = m.GetConversation()
		if m.GetExtendedTextMessage() != nil {
			body = m.GetExtendedTextMessage().GetText()
		}
		if body != "" {
			kind = "chat"
		}
		switch {
		case m.GetImageMessage() != nil:
			v := m.GetImageMessage()
			kind = "image"
			media = "photo"
			body = v.GetCaption()
			mime = v.GetMimetype()
			size = v.GetFileLength()
		case m.GetVideoMessage() != nil:
			v := m.GetVideoMessage()
			kind = "video"
			media = "video"
			body = v.GetCaption()
			mime = v.GetMimetype()
			size = v.GetFileLength()
			duration = v.GetSeconds()
			if v.GetGifPlayback() {
				kind = "gif"
				media = "animation"
			}
		case m.GetAudioMessage() != nil:
			v := m.GetAudioMessage()
			kind = "audio"
			media = "audio"
			mime = v.GetMimetype()
			size = v.GetFileLength()
			duration = v.GetSeconds()
			if v.GetPTT() {
				kind = "ptt"
				media = "voice"
			}
		case m.GetDocumentMessage() != nil:
			v := m.GetDocumentMessage()
			kind = "document"
			media = "document"
			body = v.GetCaption()
			mime = v.GetMimetype()
			size = v.GetFileLength()
			if v.GetFileName() != "" {
				filename = v.GetFileName()
			}
		case m.GetStickerMessage() != nil:
			v := m.GetStickerMessage()
			kind = "sticker"
			media = "sticker"
			mime = v.GetMimetype()
			size = v.GetFileLength()
		case m.GetContactMessage() != nil:
			kind = "vcard"
			body = m.GetContactMessage().GetDisplayName()
		case m.GetContactsArrayMessage() != nil:
			kind = "multi_vcard"
		case m.GetLocationMessage() != nil:
			kind = "location"
			body = m.GetLocationMessage().GetName()
		case m.GetPollCreationMessage() != nil:
			kind = "poll_creation"
			body = m.GetPollCreationMessage().GetName()
		case m.GetPollCreationMessageV2() != nil:
			kind = "poll_creation"
			body = m.GetPollCreationMessageV2().GetName()
		case m.GetPollCreationMessageV3() != nil:
			kind = "poll_creation"
			body = m.GetPollCreationMessageV3().GetName()
		case m.GetReactionMessage() != nil:
			kind = "reaction"
			service = true
			body = m.GetReactionMessage().GetText()
		case m.GetProtocolMessage() != nil:
			kind = "protocol"
			service = true
		}
		if ctx := messageContext(m); ctx != nil {
			forwarded = ctx.GetIsForwarded()
			if ctx.GetStanzaID() != "" {
				reply = ctx.GetStanzaID()
				replyOwner = nil
				if j, err := types.ParseJID(ctx.GetParticipant()); err == nil && !owner.IsEmpty() {
					replyOwner = identityObservation(j, append(ownerAliases, owner)...)
				}
			}
			if !owner.IsEmpty() {
				mentions = false
				for _, mention := range ctx.GetMentionedJID() {
					if j, err := types.ParseJID(mention); err == nil {
						hit := identityObservation(j, append(ownerAliases, owner)...)
						if hit == true {
							mentions = true
							break
						}
						if hit == nil {
							mentions = nil
						}
					} else {
						mentions = nil
					}
				}
			}
		}
	}
	if media != "" {
		if meta, err := nativeMediaMetadata(e); err == nil {
			filename = meta.Filename
			mime = meta.Mime
			size = meta.Bytes
		} else if e.IsViewOnce {
			media = ""
			filename = nil
			mime = nil
			size = nil
		} else {
			return nil, err
		}
	}
	if len(body) > 65536 {
		return nil, errors.New("invalid_provider_message")
	}
	if v, ok := mime.(string); ok {
		if strings.ContainsAny(v, "\r\n\x00") {
			return nil, errors.New("invalid_provider_mime")
		}
		mime = strings.ToLower(strings.TrimSpace(strings.Split(v, ";")[0]))
	}
	var self any
	if e.Info.IsGroup {
		self = false
	} else if !owner.IsEmpty() && (e.Info.Chat.Server == owner.Server || (!lidEmpty(ownerAliases) && e.Info.Chat.Server == types.HiddenUserServer)) {
		self = identityObservation(e.Info.Chat, append(ownerAliases, owner)...)
	}
	h := sha256.Sum256([]byte(body))
	observations := []any{}
	at := time.Now().UTC().Format(time.RFC3339Nano)
	if pair := observedPair(e.Info.Sender, e.Info.SenderAlt, at); pair != nil {
		observations = append(observations, pair)
	}
	if pair := observedPair(e.Info.Chat, e.Info.RecipientAlt, at); pair != nil && LegacyChatID(e.Info.Chat) != LegacyChatID(e.Info.Sender) {
		observations = append(observations, pair)
	}
	return map[string]any{"chatId": chat, "chatName": chat, "messageId": LegacyMessageID(e), "messageType": kind, "timestampResolution": "second", "durationSeconds": duration, "isService": service, "isSelfChat": self, "senderId": LegacyChatID(e.Info.Sender), "senderName": e.Info.PushName, "body": body, "timestamp": e.Info.Timestamp.Unix(), "fromMe": e.Info.IsFromMe, "hasMedia": media != "", "filename": filename, "mimetype": mime, "filesize": size, "replyToId": reply, "mentionsOwner": mentions, "replyToOwner": replyOwner, "forwarded": forwarded, "mediaKind": nilString(media), "editedTimestampMs": edited, "identityObservations": observations, "formatting": map[string]any{"version": 1, "state": "wire_observed", "basis": "whatsapp_native_wire", "wire_sha256": hex.EncodeToString(h[:]), "entities": nil, "unsupported_entities": []any{}, "warnings": []string{"syntax_interpretation_unknown"}}}, nil
}
func lidEmpty(ids []types.JID) bool {
	for _, id := range ids {
		if !id.IsEmpty() {
			return false
		}
	}
	return true
}
func identityObservation(id types.JID, owners ...types.JID) any {
	if ownedIdentity(id, owners...) {
		return true
	}
	for _, owner := range owners {
		if !owner.IsEmpty() && id.Server == owner.Server {
			return false
		}
	}
	return nil
}
func ownedIdentity(id types.JID, owners ...types.JID) bool {
	for _, owner := range owners {
		if !owner.IsEmpty() && id.ToNonAD() == owner.ToNonAD() {
			return true
		}
	}
	return false
}
func fixtureOrigin(v string) string {
	if v == "" {
		return "live"
	}
	return v
}
func nilString(v string) any {
	if v == "" {
		return nil
	}
	return v
}

func captureEnvelope(account string, e *events.Message, owner types.JID, origin string, ownerAliases ...types.JID) (map[string]any, error) {
	e = unwrap(e)
	if origin == "history" {
		origin = "backfill"
	}
	change := "message"
	var editStamp int64
	at := time.Now().UTC().Format(time.RFC3339Nano)
	if pm := e.Message.GetProtocolMessage(); pm != nil {
		if pm.GetType() == waE2E.ProtocolMessage_REVOKE {
			target := *e
			target.Info.ID = pm.GetKey().GetID()
			target.Info.IsFromMe = pm.GetKey().GetFromMe()
			if part, err := types.ParseJID(pm.GetKey().GetParticipant()); err == nil {
				target.Info.Sender = part
			}
			if target.Info.ID == "" {
				return nil, errors.New("invalid_provider_message")
			}
			return map[string]any{"kind": "whatsapp_delete", "account": account, "chat": LegacyChatID(target.Info.Chat), "ids": []string{LegacyMessageID(&target)}, "observed_at": at}, nil
		}
		if pm.GetType() == waE2E.ProtocolMessage_MESSAGE_EDIT && pm.GetEditedMessage() != nil {
			change = "edit"
			editStamp = pm.GetTimestampMS()
			copy := *e
			copy.Info.ID = pm.GetKey().GetID()
			copy.Message = pm.GetEditedMessage()
			copy.RawMessage = copy.Message
			e = &copy
		}
	}
	if e.IsEdit {
		change = "edit"
	}
	v, err := NormalizeMessage(e, owner, ownerAliases...)
	if err != nil {
		return nil, err
	}
	if change == "edit" && editStamp > 0 {
		v["editedTimestampMs"] = editStamp
	}
	return map[string]any{"kind": "whatsapp_event", "account": account, "event": v, "change": change, "observed_at": at, "origin": origin}, nil
}
