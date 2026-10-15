package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"go.mau.fi/whatsmeow/types"
)

func TestCaptureSpoolFilenameHashesExactPublishedPayload(t *testing.T) {
	s := openTestStore(t)
	if err := s.Put(fixtureMessage("wire-integrity", 1700000000), "live"); err != nil {
		t.Fatal(err)
	}
	files, err := filepath.Glob(filepath.Join(s.Config.EventsDirectory, "*.json"))
	if err != nil || len(files) != 1 {
		t.Fatal("expected one durable export", files, err)
	}
	payload, err := os.ReadFile(files[0])
	if err != nil {
		t.Fatal(err)
	}
	hash := sha256.Sum256(payload)
	if !strings.HasSuffix(files[0], "-"+hex.EncodeToString(hash[:])+".json") {
		t.Fatal("export filename is not SHA256 of the exact bytes consumed by Python")
	}
}

// Python's integration test invokes this harness to consume actual Go exports;
// no production CLI, session, network, or synthetic Python .put is involved.
func TestCaptureSpoolExportForPython(t *testing.T) {
	c := testConfig(t)
	if root := os.Getenv("ANYMSG_CAPTURE_EXPORT_DIRECTORY"); root != "" {
		if !filepath.IsAbs(root) {
			t.Fatal("fixture export directory must be absolute")
		}
		if err := os.Mkdir(root, 0700); err != nil {
			t.Fatal(err)
		}
		c.SessionDirectory = filepath.Join(root, "session")
		c.EventsDirectory = filepath.Join(root, "events")
		c.PagesDirectory = filepath.Join(root, "pages")
		for _, path := range []string{c.SessionDirectory, c.EventsDirectory, c.PagesDirectory} {
			if err := os.Mkdir(path, 0700); err != nil {
				t.Fatal(err)
			}
		}
	}
	s, err := OpenCapture(c)
	if err != nil {
		t.Fatal(err)
	}
	s.Owner = types.NewJID("123456", types.DefaultUserServer)
	for _, origin := range []string{"live", "history"} {
		message := fixtureMessage("incoming-"+origin, 1700000000)
		if err := s.Put(message, origin); err != nil {
			t.Fatal(err)
		}
	}
	outgoing := fixtureMessage("outgoing", 1700000001)
	outgoing.Info.IsFromMe = true
	outgoing.Info.Sender = s.Owner
	if err := s.Put(outgoing, "live"); err != nil {
		t.Fatal(err)
	}
	s.MaxSpoolEntries = 0
	if err := s.Put(fixtureMessage("replayed", 1700000002), "live"); !spoolBackpressure(err) {
		t.Fatal("expected durable pending capture", err)
	}
	s.Close()
	s, err = OpenCapture(c)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if err := s.Replay(); err != nil {
		t.Fatal(err)
	}
	files, err := filepath.Glob(filepath.Join(c.EventsDirectory, "*.json"))
	if err != nil || len(files) != 4 {
		t.Fatal("missing live/backfill/outgoing/replay exports", len(files), err)
	}
}

func TestJournalQuotaNeverEvictsUnconsumedLegacySemanticFilename(t *testing.T) {
	s := openTestStore(t)
	s.MaxEntries = 1
	if err := s.Put(fixtureMessage("legacy-pending", 1700000000), "live"); err != nil {
		t.Fatal(err)
	}
	files, _ := filepath.Glob(filepath.Join(s.Config.EventsDirectory, "*.json"))
	var seq int64
	var semanticDigest string
	if err := s.db.QueryRow("SELECT seq,digest FROM capture").Scan(&seq, &semanticDigest); err != nil {
		t.Fatal(err)
	}
	legacy := filepath.Join(s.Config.EventsDirectory, fmt.Sprintf("%020d-%s.json", seq, semanticDigest))
	if files[0] != legacy {
		if err := os.Rename(files[0], legacy); err != nil {
			t.Fatal(err)
		}
	}
	if err := s.Put(fixtureMessage("new", 1700000001), "live"); err == nil || err.Error() != "capture_overload" {
		t.Fatal("journal discarded an unconsumed legacy export", err)
	}
}
