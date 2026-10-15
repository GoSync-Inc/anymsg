package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
)

func mediaFixture(t *testing.T, data []byte) (mediaRequest, Config, *events.Message) {
	t.Helper()
	m := operationMessage(operationsChat, "MEDIA", false, 1700000000)
	hash := sha256.Sum256(data)
	m.Message = &waE2E.Message{DocumentMessage: &waE2E.DocumentMessage{FileLength: proto.Uint64(uint64(len(data))), FileSHA256: hash[:], FileName: proto.String("safe.pdf"), Mimetype: proto.String("Application/PDF; version=1")}}
	m.RawMessage = m.Message
	meta, e := nativeMediaMetadata(m)
	if e != nil {
		t.Fatal(e)
	}
	req := mediaRequest{Chat: operationsChat, Message: LegacyMessageID(m)}
	req.Descriptor.ProviderID = req.Message
	req.Descriptor.Bytes = meta.Bytes
	req.Descriptor.Filename = meta.Filename
	req.Descriptor.Mime = meta.Mime
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if e = os.Chmod(root, 0700); e != nil {
		t.Fatal(e)
	}
	return req, Config{PagesDirectory: root, MaxMediaBytes: 1024 * 1024}, m
}

func mediaPolicy() (Policy, error) { return allowedOperationPolicy(), nil }

// The spool parent holds no regular temp file and the private area is empty.
func requireNoNativeTemp(t *testing.T, root string) {
	t.Helper()
	for _, dir := range []string{root, filepath.Join(root, nativeTempDirectory)} {
		files, err := os.ReadDir(dir)
		if err != nil && !os.IsNotExist(err) {
			t.Fatal(err)
		}
		for _, f := range files {
			if f.Type().IsRegular() {
				t.Fatal("temporary media leaked", dir, f.Name())
			}
		}
	}
}
func mediaGet(m *events.Message) func(string, string) (*events.Message, error) {
	return func(string, string) (*events.Message, error) { return m, nil }
}

func TestNativeMediaVerifiedStreamingAndCleanup(t *testing.T) {
	data := bytes.Repeat([]byte("synthetic-only "), 10000)
	req, cfg, m := mediaFixture(t, data)
	var output bytes.Buffer
	largest := 0
	err := downloadMedia(context.Background(), req, cfg, mediaPolicy, mediaGet(m), func(_ context.Context, _ whatsmeow.DownloadableMessage, file whatsmeow.File) error {
		_, e := io.Copy(file, bytes.NewReader(data))
		return e
	}, func(chunk []byte) error {
		if len(chunk) > largest {
			largest = len(chunk)
		}
		_, e := output.Write(chunk)
		return e
	})
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(output.Bytes(), data) || largest > 65536 {
		t.Fatalf("wrong data/chunks %d", largest)
	}
	// Changed assertion: temporaries now live in the private .native-tmp area,
	// which persists empty; the spool directory itself must hold no temp file.
	requireNoNativeTemp(t, cfg.PagesDirectory)
	if req.Descriptor.Mime != "application/pdf" {
		t.Fatal("MIME was not canonicalized")
	}
}

func TestNativeMediaNeverEmitsBeforeHashAndSizeVerification(t *testing.T) {
	for _, mode := range []string{"hash", "size", "overflow"} {
		t.Run(mode, func(t *testing.T) {
			data := []byte("synthetic media bytes")
			req, cfg, m := mediaFixture(t, data)
			emitted := 0
			err := downloadMedia(context.Background(), req, cfg, mediaPolicy, mediaGet(m), func(_ context.Context, _ whatsmeow.DownloadableMessage, file whatsmeow.File) error {
				wrong := bytes.Repeat([]byte("x"), len(data))
				if mode == "size" {
					wrong = wrong[:len(wrong)-1]
				}
				if mode == "overflow" {
					wrong = bytes.Repeat([]byte("x"), int(req.Descriptor.Bytes)+65537)
				}
				_, e := io.Copy(file, bytes.NewReader(wrong))
				return e
			}, func([]byte) error { emitted++; return nil })
			want := map[string]string{"hash": "media_hash_mismatch", "size": "media_size_mismatch", "overflow": "media_size_limit"}[mode]
			requireError(t, err, want)
			if emitted != 0 {
				t.Fatal("unverified media emitted")
			}
			// Changed assertion: temporaries now live in the private .native-tmp area,
			// which persists empty; the spool directory itself must hold no temp file.
			requireNoNativeTemp(t, cfg.PagesDirectory)
		})
	}
}

func TestBoundedMediaFileCannotBypassLimitThroughCopyOrWriteAt(t *testing.T) {
	f, e := os.CreateTemp(t.TempDir(), "bounded-")
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	bounded := &boundedMediaFile{File: f, Limit: 100}
	// io.Copy normally chooses os.File.ReadFrom. A promoted method used to bypass
	// the Write cap, so exercise that interface selection as well as direct I/O.
	_, e = io.Copy(bounded, io.LimitReader(bytes.NewReader(bytes.Repeat([]byte("x"), 101)), 101))
	requireError(t, e, "media_size_limit")
	if _, e = bounded.WriteAt([]byte("x"), 101); e == nil {
		t.Fatal("WriteAt exceeded bound")
	}
	if e = bounded.Truncate(101); e == nil {
		t.Fatal("Truncate exceeded bound")
	}
	info, _ := f.Stat()
	if info.Size() > 100 {
		t.Fatal("size cap bypassed")
	}
}

func TestNativeMediaRejectsChangedDescriptorsAndViewOnceBeforeDownload(t *testing.T) {
	for _, mode := range []string{"filename", "mime", "bytes", "provider_id", "wrong_chat", "viewonce", "scope", "size_limit"} {
		t.Run(mode, func(t *testing.T) {
			req, cfg, m := mediaFixture(t, []byte("safe"))
			policy := allowedOperationPolicy()
			switch mode {
			case "filename":
				req.Descriptor.Filename = "different.pdf"
			case "mime":
				req.Descriptor.Mime = "image/png"
			case "bytes":
				req.Descriptor.Bytes++
			case "provider_id":
				req.Descriptor.ProviderID = "different"
			case "wrong_chat":
				m.Info.Chat, _ = ParseChatID("15559999999@c.us")
			case "viewonce":
				m.IsViewOnce = true
			case "scope":
				policy.ExcludeChats = []string{operationsChat}
			case "size_limit":
				cfg.MaxMediaBytes = 1
			}
			called := false
			err := downloadMedia(context.Background(), req, cfg, func() (Policy, error) { return policy, nil }, mediaGet(m), func(context.Context, whatsmeow.DownloadableMessage, whatsmeow.File) error { called = true; return nil }, func([]byte) error { t.Fatal("invalid media emitted"); return nil })
			if err == nil || called {
				t.Fatalf("invalid media fetched %v", err)
			}
		})
	}
}

func TestNativeMediaRechecksScopeAfterDownloadAndEachChunk(t *testing.T) {
	for _, revokeAt := range []string{"after_download", "after_first_chunk"} {
		t.Run(revokeAt, func(t *testing.T) {
			data := bytes.Repeat([]byte("x"), 140000)
			req, cfg, m := mediaFixture(t, data)
			policy := allowedOperationPolicy()
			emitted := 0
			err := downloadMedia(context.Background(), req, cfg, func() (Policy, error) { return policy, nil }, mediaGet(m), func(_ context.Context, _ whatsmeow.DownloadableMessage, file whatsmeow.File) error {
				_, e := file.Write(data)
				if revokeAt == "after_download" {
					policy.ExcludeChats = []string{operationsChat}
				}
				return e
			}, func([]byte) error { emitted++; policy.ExcludeChats = []string{operationsChat}; return nil })
			requireError(t, err, "chat_excluded")
			want := 0
			if revokeAt == "after_first_chunk" {
				want = 1
			}
			if emitted != want {
				t.Fatalf("revocation emitted %d chunks want %d", emitted, want)
			}
		})
	}
}

func TestNativeMediaRootSymlinkAndPublicDirectoryRejected(t *testing.T) {
	root := t.TempDir()
	if e := os.Chmod(root, 0755); e != nil {
		t.Fatal(e)
	}
	requireError(t, ensureSafeMediaRoot(root), "invalid_media_path")
	if e := os.Chmod(root, 0700); e != nil {
		t.Fatal(e)
	}
	link := filepath.Join(t.TempDir(), "link")
	if e := os.Symlink(root, link); e != nil {
		t.Fatal(e)
	}
	requireError(t, ensureSafeMediaRoot(link), "invalid_media_path")
}

func TestNativeMediaTemporaryStaysOutsideSpoolQuota(t *testing.T) {
	data := []byte("synthetic media bytes")
	req, cfg, m := mediaFixture(t, data)
	err := downloadMedia(context.Background(), req, cfg, mediaPolicy, mediaGet(m), func(_ context.Context, _ whatsmeow.DownloadableMessage, file whatsmeow.File) error {
		// While the original is in flight the spool directory itself holds no
		// regular file, so the Python spool's byte quota is unaffected.
		entries, _ := os.ReadDir(cfg.PagesDirectory)
		for _, entry := range entries {
			if entry.Type().IsRegular() {
				t.Fatal("temporary original counted by the spool quota", entry.Name())
			}
		}
		inside, _ := os.ReadDir(filepath.Join(cfg.PagesDirectory, nativeTempDirectory))
		if len(inside) != 1 || !strings.HasPrefix(inside[0].Name(), ".native-media-") {
			t.Fatal("temporary original not in the private area", inside)
		}
		_, e := file.Write(data)
		return e
	}, func([]byte) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	info, err := os.Lstat(filepath.Join(cfg.PagesDirectory, nativeTempDirectory))
	if err != nil || info.Mode().Perm() != 0700 {
		t.Fatal("private temporary area not 0700", err)
	}
	requireNoNativeTemp(t, cfg.PagesDirectory)
}

func TestNativeMediaRejectsUnsafeTemporaryArea(t *testing.T) {
	for _, mode := range []string{"symlink", "public"} {
		t.Run(mode, func(t *testing.T) {
			req, cfg, m := mediaFixture(t, []byte("safe"))
			area := filepath.Join(cfg.PagesDirectory, nativeTempDirectory)
			target := t.TempDir()
			if mode == "symlink" {
				if err := os.Symlink(target, area); err != nil {
					t.Fatal(err)
				}
			} else if err := os.Mkdir(area, 0700); err != nil || os.Chmod(area, 0755) != nil {
				t.Fatal(err)
			}
			called := false
			err := downloadMedia(context.Background(), req, cfg, mediaPolicy, mediaGet(m), func(context.Context, whatsmeow.DownloadableMessage, whatsmeow.File) error { called = true; return nil }, func([]byte) error { return nil })
			requireError(t, err, "invalid_media_path")
			if entries, _ := os.ReadDir(target); called || len(entries) != 0 {
				t.Fatal("download used an unsafe temporary area")
			}
		})
	}
}

func TestStartupSweepsOnlyStaleNativeTemporaries(t *testing.T) {
	c := testConfig(t)
	write := func(path string) {
		t.Helper()
		if err := os.WriteFile(path, []byte("stale"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	pagesArea, err := nativeTempRoot(c.PagesDirectory)
	if err != nil {
		t.Fatal(err)
	}
	stale := []string{filepath.Join(pagesArea, ".native-media-123"),
		// Left in the parent directories by releases before the private area.
		filepath.Join(c.PagesDirectory, ".native-media-legacy"), filepath.Join(c.SessionDirectory, ".history-download-legacy")}
	kept := []string{filepath.Join(c.PagesDirectory, "00000000000000000001-abc.json"), filepath.Join(c.PagesDirectory, ".pending-python"), filepath.Join(c.SessionDirectory, "auth.sqlite"), filepath.Join(pagesArea, "unrelated")}
	// A symlinked area is never followed, even with a matching name inside.
	outside := t.TempDir()
	foreign := filepath.Join(outside, ".history-download-outside")
	if err = os.Symlink(outside, filepath.Join(c.SessionDirectory, nativeTempDirectory)); err != nil {
		t.Fatal(err)
	}
	for _, path := range append(append(append([]string{}, stale...), kept...), foreign) {
		write(path)
	}
	p := &Provider{Config: c}
	if err = p.prepareCapture(); err != nil {
		t.Fatal(err)
	}
	defer p.Capture.Close()
	for _, path := range stale {
		if _, err = os.Lstat(path); !os.IsNotExist(err) {
			t.Fatal("stale temporary survived startup", path, err)
		}
	}
	for _, path := range append(kept, foreign) {
		if _, err = os.Lstat(path); err != nil {
			t.Fatal("startup sweep removed a file it does not own", path, err)
		}
	}
}
