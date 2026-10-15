package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types/events"
)

type nativeDownloadable interface {
	whatsmeow.DownloadableMessage
	GetFileLength() uint64
	GetFileSHA256() []byte
	GetMimetype() string
}

type nativeMediaInfo struct {
	Kind, Filename, Mime string
	Bytes                int64
	Payload              nativeDownloadable
}

func nativeMediaMetadata(m *events.Message) (nativeMediaInfo, error) {
	var out nativeMediaInfo
	if m == nil || m.Message == nil {
		return out, errors.New("media_unavailable")
	}
	// View-once retention must never be enabled accidentally by unwrapping it.
	if m.IsViewOnce || m.Message.GetViewOnceMessage() != nil || m.Message.GetViewOnceMessageV2() != nil || m.Message.GetViewOnceMessageV2Extension() != nil {
		return out, errors.New("unsupported_media_type")
	}
	wire := m.Message
	switch {
	case wire.GetImageMessage() != nil:
		out.Kind = "image"
		out.Payload = wire.GetImageMessage()
	case wire.GetAudioMessage() != nil:
		out.Kind = "audio"
		out.Payload = wire.GetAudioMessage()
		if wire.GetAudioMessage().GetPTT() {
			out.Kind = "ptt"
		}
	case wire.GetVideoMessage() != nil:
		out.Kind = "video"
		out.Payload = wire.GetVideoMessage()
	case wire.GetDocumentMessage() != nil:
		out.Kind = "document"
		out.Payload = wire.GetDocumentMessage()
		out.Filename = wire.GetDocumentMessage().GetFileName()
	case wire.GetStickerMessage() != nil:
		out.Kind = "sticker"
		out.Payload = wire.GetStickerMessage()
	default:
		return out, errors.New("unsupported_media_type")
	}
	// A declared size the archive cannot represent stays unknown (-1); it is
	// never clamped into a plausible value. Such an original is not fetched.
	out.Bytes = -1
	if out.Payload.GetFileLength() <= maxEnvelopeMediaBytes {
		out.Bytes = int64(out.Payload.GetFileLength())
	}
	out.Mime = envelopeMime(out.Payload.GetMimetype())
	if out.Filename == "" {
		out.Filename = envelopeText(LegacyMessageID(m), 120) + ".bin"
	}
	out.Filename = envelopeFilename(strings.Map(func(r rune) rune {
		if r < 32 || r == 127 || r == '/' || r == '\\' {
			return '_'
		}
		return r
	}, out.Filename))
	return out, nil
}

type mediaRequest struct {
	Chat       string `json:"chat"`
	Message    string `json:"message"`
	Descriptor struct {
		ProviderID string `json:"provider_id"`
		Bytes      int64  `json:"bytes"`
		Filename   string `json:"filename"`
		Mime       string `json:"mime"`
	} `json:"descriptor"`
}

type boundedMediaFile struct {
	File  *os.File
	Limit int64
}

func (f *boundedMediaFile) Read(b []byte) (int, error)            { return f.File.Read(b) }
func (f *boundedMediaFile) ReadAt(b []byte, o int64) (int, error) { return f.File.ReadAt(b, o) }
func (f *boundedMediaFile) Seek(o int64, w int) (int64, error)    { return f.File.Seek(o, w) }
func (f *boundedMediaFile) Stat() (os.FileInfo, error)            { return f.File.Stat() }

func (f *boundedMediaFile) Write(b []byte) (int, error) {
	o, err := f.File.Seek(0, io.SeekCurrent)
	if err != nil {
		return 0, err
	}
	if o < 0 || int64(len(b)) > f.Limit-o {
		return 0, errors.New("media_size_limit")
	}
	return f.File.Write(b)
}
func (f *boundedMediaFile) WriteAt(b []byte, o int64) (int, error) {
	if o < 0 || int64(len(b)) > f.Limit-o {
		return 0, errors.New("media_size_limit")
	}
	return f.File.WriteAt(b, o)
}
func (f *boundedMediaFile) Truncate(n int64) error {
	if n < 0 || n > f.Limit {
		return errors.New("media_size_limit")
	}
	return f.File.Truncate(n)
}

func (p *Provider) Media(ctx context.Context, raw json.RawMessage, emit func([]byte) error) error {
	var req mediaRequest
	if decodeStrict(raw, &req) != nil {
		return errors.New("media_reference_changed")
	}
	return downloadMedia(ctx, req, p.Config, p.policy, p.Capture.Get,
		func(ctx context.Context, m whatsmeow.DownloadableMessage, f whatsmeow.File) error {
			return p.Client.DownloadToFile(ctx, m, f)
		}, emit)
}

func downloadMedia(ctx context.Context, req mediaRequest, cfg Config, policy func() (Policy, error), get func(string, string) (*events.Message, error), download func(context.Context, whatsmeow.DownloadableMessage, whatsmeow.File) error, emit func([]byte) error) error {
	check := func() error {
		p, err := policy()
		if err != nil || !p.Enabled || !p.Allows(req.Chat) {
			return errors.New("chat_excluded")
		}
		return nil
	}
	if err := check(); err != nil {
		return err
	}
	if !validMessageReference(req.Chat, req.Message) {
		return errors.New("media_reference_changed")
	}
	m, err := get(req.Chat, req.Message)
	if err != nil || m == nil {
		return errors.New("media_unavailable")
	}
	if LegacyChatID(m.Info.Chat) != req.Chat || LegacyMessageID(m) != req.Message {
		return errors.New("media_reference_changed")
	}
	meta, err := nativeMediaMetadata(m)
	if err != nil {
		return err
	}
	if meta.Bytes < 0 {
		return errors.New("media_size_limit")
	}
	if req.Descriptor.ProviderID != req.Message || req.Descriptor.Bytes != meta.Bytes || req.Descriptor.Filename != meta.Filename || req.Descriptor.Mime != meta.Mime {
		return errors.New("media_reference_changed")
	}
	limit := cfg.MaxMediaBytes
	if limit <= 0 {
		limit = 512 * 1024 * 1024
	}
	if meta.Bytes <= 0 || meta.Bytes > limit {
		return errors.New("media_size_limit")
	}
	if len(meta.Payload.GetFileSHA256()) != 32 {
		return errors.New("media_metadata_unavailable")
	}
	root := cfg.PagesDirectory
	if root == "" {
		root = cfg.EventsDirectory
	}
	// No arbitrary path comes from an agent. Only the configured private root is used.
	if err = ensureSafeMediaRoot(root); err != nil {
		return err
	}
	var disk syscall.Statfs_t
	if err = syscall.Statfs(root, &disk); err != nil {
		return errors.New("media_network_error")
	}
	reserve := cfg.DiskReserveBytes
	if reserve < 0 {
		return errors.New("media_size_limit")
	}
	available := uint64(disk.Bavail) * uint64(disk.Bsize)
	if uint64(meta.Bytes)+65536 > available || uint64(reserve) > available-uint64(meta.Bytes)-65536 {
		return errors.New("media_size_limit")
	}
	tmpRoot, err := nativeTempRoot(root)
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(tmpRoot, ".native-media-")
	if err != nil {
		return errors.New("media_network_error")
	}
	defer os.Remove(tmp.Name())
	defer tmp.Close()
	if err = tmp.Chmod(0600); err != nil {
		return errors.New("media_network_error")
	}
	if err = check(); err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	// Native decrypt writes ciphertext then plaintext in place, so admit a small
	// cryptographic overhead but never an unbounded provider-controlled length.
	err = download(ctx, meta.Payload, &boundedMediaFile{File: tmp, Limit: meta.Bytes + 65536})
	if err != nil {
		return safeMediaError(err)
	}
	info, err := tmp.Stat()
	if err != nil {
		return errors.New("media_network_error")
	}
	if info.Size() != meta.Bytes {
		return errors.New("media_size_mismatch")
	}
	if _, err = tmp.Seek(0, io.SeekStart); err != nil {
		return errors.New("media_network_error")
	}
	h := sha256.New()
	if _, err = io.Copy(h, tmp); err != nil {
		return errors.New("media_network_error")
	}
	if !bytes.Equal(h.Sum(nil), meta.Payload.GetFileSHA256()) {
		return errors.New("media_hash_mismatch")
	}
	if _, err = tmp.Seek(0, io.SeekStart); err != nil {
		return errors.New("media_network_error")
	}
	buffer := make([]byte, 64*1024)
	for {
		if err = check(); err != nil {
			return err
		}
		if err = ctx.Err(); err != nil {
			return safeMediaError(err)
		}
		n, readErr := tmp.Read(buffer)
		if n > 0 {
			if err = emit(buffer[:n]); err != nil {
				return err
			}
		}
		if readErr == io.EOF {
			return nil
		}
		if readErr != nil {
			return errors.New("media_network_error")
		}
	}
}

func ensureSafeMediaRoot(root string) error {
	if root == "" || !filepath.IsAbs(root) {
		return errors.New("invalid_media_path")
	}
	real, err := filepath.EvalSymlinks(root)
	if err != nil || filepath.Clean(root) != real {
		return errors.New("invalid_media_path")
	}
	info, err := os.Lstat(root)
	if err != nil || !info.IsDir() || info.Mode().Perm()&0077 != 0 {
		return errors.New("invalid_media_path")
	}
	return nil
}

// Native temporaries never sit beside spool queue files: the Python spool
// counts every regular file in its top directory against its byte quota, and a
// crash left them there. It only globs *.json and sums top-level regular files,
// so this private subdirectory is outside both its quota and its drain.
const nativeTempDirectory = ".native-tmp"

var nativeTempPrefixes = []string{".native-media-", ".history-download-"}

func nativeTempRoot(parent string) (string, error) {
	dir := filepath.Join(parent, nativeTempDirectory)
	if err := os.Mkdir(dir, 0700); err != nil && !errors.Is(err, os.ErrExist) {
		return "", errors.New("invalid_media_path")
	}
	info, err := os.Lstat(dir)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 || info.Mode().Perm()&0077 != 0 {
		return "", errors.New("invalid_media_path")
	}
	return dir, nil
}

// sweepNativeTemp removes temporaries left by a crashed or killed provider,
// including ones from releases that wrote them into the parent directory. The
// caller holds the session lock, so no other provider of this account writes
// there. Only our own regular files are removed and no symlink is followed.
func sweepNativeTemp(cfg Config) {
	media := cfg.PagesDirectory
	if media == "" {
		media = cfg.EventsDirectory
	}
	for _, parent := range []string{media, cfg.SessionDirectory} {
		if parent == "" {
			continue
		}
		for _, dir := range []string{filepath.Join(parent, nativeTempDirectory), parent} {
			if info, err := os.Lstat(dir); err != nil || !info.IsDir() {
				continue
			}
			entries, err := os.ReadDir(dir)
			if err != nil {
				continue
			}
			for _, entry := range entries {
				for _, prefix := range nativeTempPrefixes {
					if entry.Type().IsRegular() && strings.HasPrefix(entry.Name(), prefix) {
						_ = os.Remove(filepath.Join(dir, entry.Name()))
					}
				}
			}
		}
	}
}

func safeMediaError(err error) error {
	switch {
	case errors.Is(err, context.Canceled), errors.Is(err, context.DeadlineExceeded):
		return errors.New("media_timeout")
	case errors.Is(err, whatsmeow.ErrInvalidMediaSHA256), errors.Is(err, whatsmeow.ErrInvalidMediaEncSHA256), errors.Is(err, whatsmeow.ErrInvalidUnencryptedMediaSHA256), errors.Is(err, whatsmeow.ErrInvalidMediaHMAC):
		return errors.New("media_hash_mismatch")
	case errors.Is(err, whatsmeow.ErrMediaDownloadFailedWith403), errors.Is(err, whatsmeow.ErrMediaDownloadFailedWith404), errors.Is(err, whatsmeow.ErrMediaDownloadFailedWith410):
		return errors.New("media_unavailable")
	case err.Error() == "media_size_limit":
		return errors.New("media_size_limit")
	default:
		return errors.New("media_network_error")
	}
}

func digestBytes(value []byte) string { h := sha256.Sum256(value); return hex.EncodeToString(h[:]) }
