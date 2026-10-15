package main

import (
	"bytes"
	"compress/zlib"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	waCommon "go.mau.fi/whatsmeow/proto/waCommon"
	waE2E "go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func testConfig(t *testing.T) Config {
	t.Helper()
	root := t.TempDir()
	_ = os.Chmod(root, 0700)
	c := Config{Account: "test", ExpectedWID: "123456@c.us", SessionDirectory: filepath.Join(root, "session"), EventsDirectory: filepath.Join(root, "events"), PagesDirectory: filepath.Join(root, "pages"), MaxMediaBytes: 8 << 20}
	for _, d := range []string{c.SessionDirectory, c.EventsDirectory, c.PagesDirectory} {
		if e := os.Mkdir(d, 0700); e != nil {
			t.Fatal(e)
		}
	}
	return c
}
func fixtureMessage(id string, stamp int64) *events.Message {
	chat := types.NewJID("111111", types.DefaultUserServer)
	return &events.Message{Info: types.MessageInfo{MessageSource: types.MessageSource{Chat: chat, Sender: chat}, ID: id, Timestamp: time.Unix(stamp, 0)}, Message: &waE2E.Message{Conversation: proto.String("hello")}}
}
func openTestStore(t *testing.T) *CaptureStore {
	t.Helper()
	s, e := OpenCapture(testConfig(t))
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { s.Close() })
	s.Owner = types.NewJID("123456", types.DefaultUserServer)
	return s
}
func savePolicy(t *testing.T, p *Provider, expires time.Time, policy Policy) {
	t.Helper()
	b, _ := json.Marshal(map[string]any{"expires_at": float64(expires.UnixNano()) / 1e9, "policy": policy})
	if e := os.WriteFile(filepath.Join(p.Config.EventsDirectory, ".policy"), b, 0600); e != nil {
		t.Fatal(e)
	}
}
func TestPolicyFreshFileAndExclusions(t *testing.T) {
	p := &Provider{Config: testConfig(t)}
	allow := Policy{Enabled: true, DiscoverChats: true, IncludeChats: []string{}, ExcludeChats: []string{"111111@c.us"}}
	savePolicy(t, p, time.Now().Add(time.Minute), allow)
	policy, e := p.policy()
	if e != nil || policy.Allows("111111@c.us") || !policy.Allows("222222@c.us") {
		t.Fatal(policy, e)
	}
	savePolicy(t, p, time.Now().Add(-time.Second), allow)
	if _, e = p.policy(); e == nil || e.Error() != "policy_expired" {
		t.Fatal(e)
	}
	savePolicy(t, p, time.Now().Add(time.Minute), allow)
	os.Chmod(filepath.Join(p.Config.EventsDirectory, ".policy"), 0644)
	if _, e = p.policy(); e == nil {
		t.Fatal("public policy accepted")
	}
}

func TestEveryLifecycleClientDefersACKUntilDurableHandling(t *testing.T) {
	// Exercise the actual constructor used by initial pairing, restored pairing,
	// and the normal collector, without connecting or creating a session.
	client := nativeClient(&store.Device{})
	if !client.SynchronousAck || !client.EnableDecryptedEventBuffer {
		t.Fatal("decrypt/ACK can consume a message before durable capture")
	}
	if !client.ManualHistorySyncDownload || !client.DisableManualHistorySyncReceipt {
		t.Fatal("history notification can escape the durable bounded queue")
	}
}
func TestPolicySymlinkRejected(t *testing.T) {
	p := &Provider{Config: testConfig(t)}
	target := filepath.Join(p.Config.SessionDirectory, "target")
	os.WriteFile(target, []byte(`{}`), 0600)
	os.Symlink(target, filepath.Join(p.Config.EventsDirectory, ".policy"))
	if _, e := p.policy(); e == nil {
		t.Fatal("symlink accepted")
	}
}
func TestIDsRetainObservedNamespaces(t *testing.T) {
	for _, id := range []string{"111111@c.us", "111111@lid", "111111-222@g.us"} {
		j, e := ParseChatID(id)
		if e != nil || LegacyChatID(j) != id {
			t.Fatal(id, j, e)
		}
	}
	if _, e := ParseChatID("111111:4@c.us"); e == nil {
		t.Fatal("AD accepted")
	}
	m := fixtureMessage("abc", 1700000000)
	if LegacyMessageID(m) != "false_111111@c.us_abc" {
		t.Fatal(LegacyMessageID(m))
	}
	m.Info.Chat = types.NewJID("123-456", types.GroupServer)
	m.Info.IsGroup = true
	if LegacyMessageID(m) != "false_123-456@g.us_abc_111111@c.us" {
		t.Fatal(LegacyMessageID(m))
	}
}
func TestNativeMessageMetadataOwnerLIDAndNoMillisecondInvention(t *testing.T) {
	m := fixtureMessage("a", 1700000000)
	owner := types.NewJID("123456", types.DefaultUserServer)
	lid := types.NewJID("999", types.HiddenUserServer)
	m.Message = &waE2E.Message{ExtendedTextMessage: &waE2E.ExtendedTextMessage{Text: proto.String("hello"), ContextInfo: &waE2E.ContextInfo{MentionedJID: []string{lid.String()}, StanzaID: proto.String("original"), Participant: proto.String(lid.String())}}}
	v, e := NormalizeMessage(m, owner, lid)
	if e != nil || v["mentionsOwner"] != true || v["replyToOwner"] != true || v["timestampResolution"] != "second" {
		t.Fatal(v, e)
	}
	v, e = NormalizeMessage(m, owner)
	if e != nil || v["mentionsOwner"] != nil || v["replyToOwner"] != nil {
		t.Fatal("unknown namespace fabricated", v, e)
	}
}
func TestMediaDescriptorExactFallbackAndFilename(t *testing.T) {
	m := fixtureMessage("media", 1700000000)
	m.Message = &waE2E.Message{DocumentMessage: &waE2E.DocumentMessage{FileName: proto.String("../../doc\n.pdf"), Mimetype: proto.String("application/pdf"), FileLength: proto.Uint64(8)}}
	v, e := NormalizeMessage(m, types.EmptyJID)
	if e != nil || v["filename"] != ".._.._doc_.pdf" || v["filesize"] != int64(8) || v["messageType"] != "document" {
		t.Fatal(v, e)
	}
}
func TestHistoryOriginAcceptedByArchiveContract(t *testing.T) {
	v, e := captureEnvelope("test", fixtureMessage("a", 1700000000), types.EmptyJID, "history")
	if e != nil || v["origin"] != "backfill" {
		t.Fatal(v, e)
	}
}

func TestReceiptsRetainOnlyObservedOutgoingEvidence(t *testing.T) {
	s := openTestStore(t)
	m := fixtureMessage("out", 1700000000)
	m.Info.IsFromMe = true
	m.Info.Sender = s.Owner
	if err := s.Put(m, "live"); err != nil {
		t.Fatal(err)
	}
	if err := s.Put(fixtureMessage("in", 1700000001), "live"); err != nil {
		t.Fatal(err)
	}
	receipt := &events.Receipt{MessageSource: types.MessageSource{Chat: m.Info.Chat}, MessageIDs: []types.MessageID{"out", "in", "absent"}, Timestamp: time.Unix(1700000010, 0), Type: types.ReceiptTypeRead}
	if err := s.StoreReceipt(receipt); err != nil {
		t.Fatal(err)
	}
	got, err := s.ReceiptObservations(LegacyChatID(m.Info.Chat), LegacyMessageID(m))
	if err != nil || len(got) != 1 || got[0]["state"] != "read" || got[0]["provider_at"] != nil || got[0]["audience"] != "private_peer" {
		t.Fatal(got, err)
	}
	got, err = s.ReceiptObservations(LegacyChatID(m.Info.Chat), "false_111111@c.us_in")
	if err != nil || len(got) != 0 {
		t.Fatal("incoming receipt fabricated", got, err)
	}
}
func TestDurableDedupeAndLocalPage(t *testing.T) {
	s := openTestStore(t)
	for _, id := range []string{"b", "a"} {
		if e := s.Put(fixtureMessage(id, 1700000000), "live"); e != nil {
			t.Fatal(e)
		}
	}
	if e := s.Put(fixtureMessage("a", 1700000000), "history"); e != nil {
		t.Fatal(e)
	}
	var n int
	s.db.QueryRow("SELECT COUNT(*) FROM capture").Scan(&n)
	if n != 2 {
		t.Fatal("replay duplicate", n)
	}
	page, e := s.Page("111111@c.us", 1, nil)
	if e != nil || len(page) != 1 || page[0].Info.ID != "b" {
		t.Fatal(page, e)
	}
	page, e = s.Page("111111@c.us", 1, []any{float64(1700000000), LegacyMessageID(page[0])})
	if e != nil || len(page) != 1 || page[0].Info.ID != "a" {
		t.Fatal(page, e)
	}
}
func TestSpoolFailureSurvivesRestartReplay(t *testing.T) {
	c := testConfig(t)
	s, e := OpenCapture(c)
	if e != nil {
		t.Fatal(e)
	}
	s.MaxSpoolEntries = 0
	if e = s.Put(fixtureMessage("a", 1700000000), "live"); e == nil || e.Error() != "spool_full" {
		t.Fatal(e)
	}
	s.Close()
	s, e = OpenCapture(c)
	if e != nil {
		t.Fatal(e)
	}
	defer s.Close()
	if e = s.Replay(); e != nil {
		t.Fatal(e)
	}
	entries, _ := os.ReadDir(c.EventsDirectory)
	if len(entries) != 1 || !strings.HasSuffix(entries[0].Name(), ".json") {
		t.Fatal(entries)
	}
	var published int
	s.db.QueryRow("SELECT published FROM capture").Scan(&published)
	if published != 1 {
		t.Fatal(published)
	}
}

func TestActuallyFullSpoolAllowsReadyAndRecoversAfterConsumerDrain(t *testing.T) {
	c := testConfig(t)
	s, err := OpenCapture(c)
	if err != nil {
		t.Fatal(err)
	}
	// A real existing spool entry consumes the quota; a second message is
	// durably journalled but cannot be exported, as during an archive DB outage.
	s.MaxSpoolEntries = 1
	if err = s.Put(fixtureMessage("already-exported", 1700000000), "live"); err != nil {
		t.Fatal(err)
	}
	if err = s.Put(fixtureMessage("pending", 1700000001), "live"); !spoolBackpressure(err) {
		t.Fatal("expected actually full existing spool", err)
	}
	files, err := os.ReadDir(c.EventsDirectory)
	if err != nil || len(files) != 1 {
		t.Fatal(files, err)
	}
	fullName := files[0].Name()
	s.Close()

	// Fill the actual production count boundary before reopening. Copies of an
	// exported capture stand in for the remaining unconsumed files, so startup
	// uses its real 10000-entry quota rather than a raised test-only capacity.
	exported, err := os.ReadFile(filepath.Join(c.EventsDirectory, fullName))
	if err != nil {
		t.Fatal(err)
	}
	for i := 1; i < 10000; i++ {
		if err = os.WriteFile(filepath.Join(c.EventsDirectory, fmt.Sprintf("filler-%05d.json", i)), exported, 0600); err != nil {
			t.Fatal(err)
		}
	}
	p := &Provider{Config: c}
	if err = p.prepareCapture(); err != nil {
		t.Fatal("full spool prevents provider startup/readiness", err)
	}
	defer p.Capture.Close()
	p.ready.Store(true) // the real Connected handler sets this after identity verification
	savePolicy(t, p, time.Now().Add(time.Minute), Policy{Enabled: true, DiscoverChats: true, IncludeChats: []string{}, ExcludeChats: []string{}})
	if _, err = p.handle(context.Background(), request{ID: 1, Method: "policy", Params: json.RawMessage(`{}`)}, func(any) error { return nil }); err != nil {
		t.Fatal("consumer cannot start after connected readiness", err)
	}
	p.captureFailure(errors.New("spool_full"))
	if !p.ready.Load() {
		t.Fatal("backpressure cancelled ready provider before consumer drain")
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { p.replayWorker(ctx, 10*time.Millisecond); close(done) }()
	defer func() { cancel(); <-done }()
	// The archive consumer commits the old event, then removes its spool file.
	if err = os.Remove(filepath.Join(c.EventsDirectory, fullName)); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(5 * time.Second)
	var pending int
	for time.Now().Before(deadline) {
		if err = p.Capture.db.QueryRow("SELECT COUNT(*) FROM capture WHERE published=0").Scan(&pending); err != nil {
			t.Fatal(err)
		}
		if pending == 0 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if pending != 0 || !p.ready.Load() {
		t.Fatal("pending capture did not recover after consumer freed capacity", pending)
	}
	var count int
	p.Capture.db.QueryRow("SELECT COUNT(*) FROM capture").Scan(&count)
	if count != 2 {
		t.Fatal("lost or duplicated journal captures", count)
	}
	files, _ = os.ReadDir(c.EventsDirectory)
	var recovered int
	for _, f := range files {
		if strings.HasPrefix(f.Name(), "00000000000000000002-") {
			recovered++
		}
	}
	if recovered != 1 {
		t.Fatal("pending capture exported more/less than once", recovered)
	}
}

func TestReplayPublishesBoundedOldestBatches(t *testing.T) {
	s := openTestStore(t)
	s.MaxSpoolEntries = 0
	const total = 2*replayBatchEntries + 1
	for i := 0; i < total; i++ {
		if err := s.Put(fixtureMessage(fmt.Sprintf("pending-%03d", i), 1700000000+int64(i)), "live"); !spoolBackpressure(err) {
			t.Fatal("capture was not durably queued", err)
		}
	}
	s.MaxSpoolEntries = 10000
	for _, want := range []int{replayBatchEntries, 2 * replayBatchEntries, total} {
		if err := s.Replay(); err != nil {
			t.Fatal(err)
		}
		var count, maxSeq int
		var bytes int64
		if err := s.db.QueryRow("SELECT COUNT(*),MAX(seq),SUM(length(envelope)) FROM capture WHERE published=1").Scan(&count, &maxSeq, &bytes); err != nil {
			t.Fatal(err)
		}
		if count != want || maxSeq != want {
			t.Fatal("replay skipped or exceeded oldest-first batch", count, maxSeq, want)
		}
		if want == replayBatchEntries && bytes > replayBatchBytes {
			t.Fatal("first batch exceeded byte budget", bytes)
		}
	}
}

func TestReplayBatchSharesConservativeSpoolQuota(t *testing.T) {
	s := openTestStore(t)
	s.MaxSpoolEntries = 0
	for i := 0; i < 5; i++ {
		if err := s.Put(fixtureMessage(fmt.Sprintf("quota-%d", i), 1700000000+int64(i)), "live"); !spoolBackpressure(err) {
			t.Fatal(err)
		}
	}
	s.MaxSpoolEntries = 2
	if err := s.Replay(); !spoolBackpressure(err) {
		t.Fatal("batch exceeded count quota", err)
	}
	files, err := os.ReadDir(s.Config.EventsDirectory)
	if err != nil || len(files) != 2 {
		t.Fatal("shared budget did not count newly exported files", len(files), err)
	}
	var count, maxSeq int
	if err = s.db.QueryRow("SELECT COUNT(*),MAX(seq) FROM capture WHERE published=1").Scan(&count, &maxSeq); err != nil || count != 2 || maxSeq != 2 {
		t.Fatal("quota interrupted batch lost oldest-first progress", count, maxSeq, err)
	}
}

func TestSpoolMeasurementToleratesConcurrentConsumerUnlink(t *testing.T) {
	dir := t.TempDir()
	for _, name := range []string{"committed.json", "pending.json"} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(`{"event":"fixture"}`), 0600); err != nil {
			t.Fatal(err)
		}
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	// Deterministically exercise the real race window: directory snapshot first,
	// consumer commit/unlink second, per-entry stat last.
	if err = os.Remove(filepath.Join(dir, "committed.json")); err != nil {
		t.Fatal(err)
	}
	usage, err := measureSpool(entries)
	if err != nil || usage.count != 1 || usage.bytes != int64(len(`{"event":"fixture"}`)) {
		t.Fatal("normal consumer drain became a fatal capture error", usage, err)
	}
	if err = os.Symlink("pending.json", filepath.Join(dir, "unexpected-link")); err != nil {
		t.Fatal(err)
	}
	entries, err = os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = measureSpool(entries); err == nil || err.Error() != "spool_symlink" {
		t.Fatal("unlink tolerance also accepted an unsafe directory entry", err)
	}
}

func TestJournalQuotaNeverEvictsUnconsumedSpool(t *testing.T) {
	s := openTestStore(t)
	s.MaxEntries = 1
	if e := s.Put(fixtureMessage("a", 1700000000), "live"); e != nil {
		t.Fatal(e)
	}
	if e := s.Put(fixtureMessage("b", 1700000001), "live"); e == nil || e.Error() != "capture_overload" {
		t.Fatal(e)
	}
	files, _ := os.ReadDir(s.Config.EventsDirectory)
	for _, f := range files {
		os.Remove(filepath.Join(s.Config.EventsDirectory, f.Name()))
	}
	if e := s.Put(fixtureMessage("b", 1700000001), "live"); e != nil {
		t.Fatal("consumed bounded cache not reclaimed", e)
	}
}
func TestEditTargetAndWireTimeStableAcrossReplay(t *testing.T) {
	s := openTestStore(t)
	original := fixtureMessage("original", 1700000000)
	s.Put(original, "live")
	edit := fixtureMessage("editid", 1700000100)
	edit.Message = &waE2E.Message{ProtocolMessage: &waE2E.ProtocolMessage{Type: waE2E.ProtocolMessage_MESSAGE_EDIT.Enum(), Key: &waCommon.MessageKey{ID: proto.String("original"), FromMe: proto.Bool(false)}, EditedMessage: &waE2E.Message{Conversation: proto.String("edited")}, TimestampMS: proto.Int64(1700000100123)}}
	if e := s.Put(edit, "live"); e != nil {
		t.Fatal(e)
	}
	if e := s.Put(edit, "history"); e != nil {
		t.Fatal(e)
	}
	m, e := s.Get("111111@c.us", LegacyMessageID(original))
	if e != nil || m.Message.GetConversation() != "edited" || m.Info.Timestamp != original.Info.Timestamp {
		t.Fatal(m, e)
	}
	v, e := NormalizeMessage(m, s.Owner)
	if e != nil || v["editedTimestampMs"] != int64(1700000100123) {
		t.Fatal(v, e)
	}
	var n int
	s.db.QueryRow("SELECT COUNT(*) FROM capture").Scan(&n)
	if n != 2 {
		t.Fatal("edit replay changed", n)
	}
}
func TestGlobalRevokeNoBlankHumanMessage(t *testing.T) {
	s := openTestStore(t)
	m := fixtureMessage("original", 1700000000)
	s.Put(m, "live")
	revoke := fixtureMessage("revokeid", 1700000001)
	revoke.Message = &waE2E.Message{ProtocolMessage: &waE2E.ProtocolMessage{Type: waE2E.ProtocolMessage_REVOKE.Enum(), Key: &waCommon.MessageKey{ID: proto.String("original"), FromMe: proto.Bool(false)}}}
	if e := s.Put(revoke, "live"); e != nil {
		t.Fatal(e)
	}
	if _, e := s.Get("111111@c.us", LegacyMessageID(m)); e == nil {
		t.Fatal("revoked target readable")
	}
	page, e := s.Page("111111@c.us", 10, nil)
	if e != nil || len(page) != 0 {
		t.Fatal(page, e)
	}
}
func TestPairRequiresTerminalBeforeOpeningAuth(t *testing.T) {
	c := testConfig(t)
	if info, _ := os.Stderr.Stat(); info.Mode()&os.ModeCharDevice != 0 {
		t.Skip("running on TTY")
	}
	if e := pairSession(context.Background(), c); e == nil || e.Error() != "owner_terminal_required" {
		t.Fatal(e)
	}
	if _, e := os.Stat(filepath.Join(c.SessionDirectory, "auth.sqlite")); !errors.Is(e, os.ErrNotExist) {
		t.Fatal("opened auth before terminal gate", e)
	}
}
func TestExclusiveNativeSessionLock(t *testing.T) {
	c := testConfig(t)
	one, e := acquire(c.SessionDirectory)
	if e != nil {
		t.Fatal(e)
	}
	defer one.Close()
	if two, e := acquire(c.SessionDirectory); e == nil {
		two.Close()
		t.Fatal("duplicate owner")
	}
}

func TestPageUsesExactCollectorContractAndNeverClaimsComplete(t *testing.T) {
	s := openTestStore(t)
	s.Put(fixtureMessage("a", 1700000000), "live")
	p := &Provider{Config: s.Config, Capture: s}
	p.ready.Store(true)
	savePolicy(t, p, time.Now().Add(time.Minute), Policy{Enabled: true, DiscoverChats: true, IncludeChats: []string{}, ExcludeChats: []string{}})
	// A ready synthetic provider may omit its client. Exercise pure page shaping.
	result, e := p.handle(context.Background(), request{ID: 1, Method: "page", Params: json.RawMessage(`{"chat":"111111@c.us","limit":10}`)}, func(any) error { return nil })
	if e != nil {
		t.Fatal(e)
	}
	v := result.(map[string]any)
	if len(v) != 3 || v["messages"] == nil || v["exhausted"] != false || v["pending"] != true {
		t.Fatal(v)
	}
}
func TestScopedPolicyStillDurablyQueuesHistoryControl(t *testing.T) {
	s := openTestStore(t)
	p := &Provider{Config: s.Config, Capture: s}
	savePolicy(t, p, time.Now().Add(time.Minute), Policy{Enabled: true, IncludeChats: []string{"222222@c.us"}, ExcludeChats: []string{}})
	notification := fixtureMessage("control", 1700000000)
	notification.Message = &waE2E.Message{ProtocolMessage: &waE2E.ProtocolMessage{HistorySyncNotification: &waE2E.HistorySyncNotification{FileLength: proto.Uint64(100)}}}
	if e := p.handleMessage(notification); e != nil {
		t.Fatal(e)
	}
	var n int
	s.db.QueryRow("SELECT COUNT(*) FROM history_jobs").Scan(&n)
	if n != 1 {
		t.Fatal("history control discarded by chat scope", n)
	}
	s.db.QueryRow("SELECT COUNT(*) FROM capture").Scan(&n)
	if n != 0 {
		t.Fatal("excluded control message in archive", n)
	}
}

func TestHistoryDownloadRetriesResumeAfterCrashWithPersistedBackoff(t *testing.T) {
	c := testConfig(t)
	s, err := OpenCapture(c)
	if err != nil {
		t.Fatal(err)
	}
	notification := fixtureMessage("retry-control", 1700000000)
	notification.Message = &waE2E.Message{ProtocolMessage: &waE2E.ProtocolMessage{HistorySyncNotification: &waE2E.HistorySyncNotification{FileLength: proto.Uint64(100)}}}
	if err = s.EnqueueHistory(notification); err != nil {
		t.Fatal(err)
	}
	now := time.Unix(1700000100, 0)
	first, err := s.claimHistory(now)
	if err != nil || first.Attempt != 1 || first.Next != now.Add(time.Minute).UnixMilli() {
		t.Fatal(first, err)
	}
	// Simulate a crash after only a prefix was durably captured. No failure
	// callback runs. The same notification and its attempt survive reopening.
	if err = s.Put(fixtureMessage("prefix", 1700000000), "history"); err != nil {
		t.Fatal(err)
	}
	s.Close()
	s, err = OpenCapture(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if _, err = s.claimHistory(now.Add(30 * time.Second)); !errors.Is(err, sql.ErrNoRows) {
		t.Fatal("retried before persisted backoff", err)
	}
	second, err := s.claimHistory(now.Add(time.Minute))
	if err != nil || second.Attempt != 2 {
		t.Fatal(second, err)
	}
	if err = s.Put(fixtureMessage("prefix", 1700000000), "history"); err != nil {
		t.Fatal(err)
	}
	var count int
	s.db.QueryRow("SELECT COUNT(*) FROM capture").Scan(&count)
	if count != 1 {
		t.Fatal("partial history replay created duplicate", count)
	}
	if err = s.historyFailure(second, "history_download_failed"); err != nil {
		t.Fatal(err)
	}
	third, err := s.claimHistory(time.UnixMilli(second.Next))
	if err != nil || third.Attempt != maxHistoryAttempts {
		t.Fatal(third, err)
	}
	// A crash during the last attempt also reaches a terminal, visible state.
	if _, err = s.claimHistory(time.UnixMilli(third.Next)); !errors.Is(err, sql.ErrNoRows) {
		t.Fatal("unbounded history retry", err)
	}
	var state, reason string
	s.db.QueryRow("SELECT state,error FROM history_jobs WHERE stanza='retry-control'").Scan(&state, &reason)
	if state != "failed" || reason != "history_attempts_exhausted" {
		t.Fatal(state, reason)
	}
	status, err := readPrivate(filepath.Join(c.EventsDirectory, ".history-status"), 4096)
	if err != nil || !strings.Contains(string(status), `"state":"failed"`) {
		t.Fatal(string(status), err)
	}
}

func TestHistoryKnownDownloadFailuresStopAfterThreeAttempts(t *testing.T) {
	s := openTestStore(t)
	n := fixtureMessage("retry-control", 1700000000)
	n.Message = &waE2E.Message{ProtocolMessage: &waE2E.ProtocolMessage{HistorySyncNotification: &waE2E.HistorySyncNotification{FileLength: proto.Uint64(100)}}}
	if err := s.EnqueueHistory(n); err != nil {
		t.Fatal(err)
	}
	now := time.Unix(1700000100, 0)
	for i := 1; i <= maxHistoryAttempts; i++ {
		job, err := s.claimHistory(now)
		if err != nil || job.Attempt != i {
			t.Fatal(job, err)
		}
		if err = s.historyFailure(job, "history_download_failed"); err != nil {
			t.Fatal(err)
		}
		now = time.UnixMilli(job.Next)
	}
	if _, err := s.claimHistory(now.Add(time.Hour)); !errors.Is(err, sql.ErrNoRows) {
		t.Fatal("retried a terminal download failure", err)
	}
}

func TestExactOutgoingRetryPayloadSurvivesRestartAndExpires(t *testing.T) {
	c := testConfig(t)
	s, err := OpenCapture(c)
	if err != nil {
		t.Fatal(err)
	}
	chat := types.NewJID("111111", types.DefaultUserServer)
	wire, _ := proto.Marshal(&waE2E.Message{Conversation: proto.String("exact original")})
	b := &nativeEventBuffer{Capture: s}
	if err = b.AddOutgoingEvent(context.Background(), chat, "same-stanza", "wa", wire); err != nil {
		t.Fatal(err)
	}
	s.Close()
	s, err = OpenCapture(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	b = &nativeEventBuffer{Capture: s}
	format, got, err := b.GetOutgoingEvent(context.Background(), chat, types.EmptyJID, "same-stanza")
	if err != nil || format != "wa" || !bytes.Equal(got, wire) {
		t.Fatal(format, got, err)
	}
	if _, _, err = b.GetOutgoingEvent(context.Background(), types.NewJID("222222", types.DefaultUserServer), types.EmptyJID, "same-stanza"); !errors.Is(err, sql.ErrNoRows) {
		t.Fatal("cross-chat payload lookup", err)
	}
	s.db.Exec("UPDATE outgoing_retry SET created_at=?", time.Now().Add(-8*24*time.Hour).UnixMilli())
	if _, _, err = b.GetOutgoingEvent(context.Background(), chat, types.EmptyJID, "same-stanza"); !errors.Is(err, sql.ErrNoRows) {
		t.Fatal("expired payload returned", err)
	}
	if err = b.DeleteOldOutgoingEvents(context.Background()); err != nil {
		t.Fatal(err)
	}
	var count int
	s.db.QueryRow("SELECT COUNT(*) FROM outgoing_retry").Scan(&count)
	if count != 0 {
		t.Fatal("expired payload retained", count)
	}
}

func TestOutgoingRetryCacheEnforcesCountAndByteBounds(t *testing.T) {
	s := openTestStore(t)
	b := &nativeEventBuffer{Capture: s}
	chat := types.NewJID("111111", types.DefaultUserServer)
	ctx := context.Background()
	// Populate the count boundary in one transaction, then exercise the actual
	// insertion/eviction path. Oldest content may leave this retry cache only;
	// archive captures and unconsumed spool records use their separate journal.
	tx, _ := s.db.Begin()
	for i := 0; i < maxOutgoingRetryMessages; i++ {
		if _, err := tx.Exec("INSERT INTO outgoing_retry VALUES(?,?,?,?,?)", chat.String(), strconv.Itoa(i), "wa", []byte("old"), time.Now().Add(-time.Minute).UnixMilli()); err != nil {
			t.Fatal(err)
		}
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
	if err := b.AddOutgoingEvent(ctx, chat, "new", "wa", []byte("retained")); err != nil {
		t.Fatal(err)
	}
	var count, size int64
	s.db.QueryRow("SELECT COUNT(*),SUM(length(payload))FROM outgoing_retry").Scan(&count, &size)
	if count != maxOutgoingRetryMessages {
		t.Fatal(count)
	}
	if _, got, err := b.GetOutgoingEvent(ctx, chat, types.EmptyJID, "new"); err != nil || string(got) != "retained" {
		t.Fatal(got, err)
	}
	s.db.Exec("DELETE FROM outgoing_retry")
	tx, _ = s.db.Begin()
	for i := 0; i < maxOutgoingRetryBytes/(512<<10); i++ {
		if _, err := tx.Exec("INSERT INTO outgoing_retry VALUES(?,?,?,?,?)", chat.String(), strconv.Itoa(i), "wa", bytes.Repeat([]byte{1}, 512<<10), time.Now().Add(-time.Minute).UnixMilli()); err != nil {
			t.Fatal(err)
		}
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
	if err := b.AddOutgoingEvent(ctx, chat, "new", "wa", []byte("retained")); err != nil {
		t.Fatal(err)
	}
	s.db.QueryRow("SELECT COUNT(*),SUM(length(payload))FROM outgoing_retry").Scan(&count, &size)
	if size > maxOutgoingRetryBytes || count > maxOutgoingRetryMessages {
		t.Fatal(count, size)
	}
}

func TestProtocolRetryIsFencedByFreshIdentityReadinessAndPolicy(t *testing.T) {
	s := openTestStore(t)
	owner := s.Owner
	p := &Provider{Config: s.Config, Capture: s, Client: nativeClient(&store.Device{ID: &owner})}
	receipt := &events.Receipt{MessageSource: types.MessageSource{Chat: types.NewJID("111111", types.DefaultUserServer)}}
	allow := Policy{Enabled: true, SendEnabled: true, DiscoverChats: true, IncludeChats: []string{}, ExcludeChats: []string{}}
	savePolicy(t, p, time.Now().Add(time.Minute), allow)
	if p.retryAllowed(receipt) {
		t.Fatal("retry before connected")
	}
	p.ready.Store(true)
	if !p.retryAllowed(receipt) {
		t.Fatal("scoped original-stanza retry denied")
	}
	allow.ExcludeChats = []string{"111111@c.us"}
	savePolicy(t, p, time.Now().Add(time.Minute), allow)
	if p.retryAllowed(receipt) {
		t.Fatal("retry after scope revocation")
	}
	allow.ExcludeChats = []string{}
	savePolicy(t, p, time.Now().Add(-time.Second), allow)
	if p.retryAllowed(receipt) {
		t.Fatal("retry with expired policy")
	}
	savePolicy(t, p, time.Now().Add(time.Minute), allow)
	p.Config.ExpectedWID = "222222@c.us"
	if p.retryAllowed(receipt) {
		t.Fatal("retry from wrong account")
	}
}

func TestHistoryDecompressionRejectsBombAndCorruptPayload(t *testing.T) {
	var compressed bytes.Buffer
	writer := zlib.NewWriter(&compressed)
	if _, err := writer.Write(bytes.Repeat([]byte("A"), 1<<20)); err != nil {
		t.Fatal(err)
	}
	writer.Close()
	if err := validateCompressedHistory(compressed.Bytes(), 1024); err == nil || err.Error() != "history_size_limit" {
		t.Fatal("decompression bomb accepted", err)
	}
	if err := validateCompressedHistory(compressed.Bytes(), 1<<20); err != nil {
		t.Fatal("bounded payload rejected", err)
	}
	if err := validateCompressedHistory(compressed.Bytes()[:compressed.Len()-2], 1<<20); err == nil {
		t.Fatal("truncated zlib accepted")
	}
}
