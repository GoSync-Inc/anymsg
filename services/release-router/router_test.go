package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

const oldID = "111111111111"
const newID = "222222222222"
const token = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

func backend(t *testing.T, name string) *httptest.Server {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]string{"backend": name, "path": r.URL.RequestURI(), "host": r.Host, "authorization": r.Header.Get("Authorization")})
	}))
	t.Cleanup(server.Close)
	return server
}
func address(server *httptest.Server) string { return strings.TrimPrefix(server.URL, "http://") }
func config(api, gateway string) Config {
	return Config{Version: 1, Listen: ":8080", AdminSocket: "/run/deedkit-router/admin.sock", Active: oldID, Legacy: Targets{API: api, Gateway: gateway}, Generations: map[string]Generation{oldID: {API: api, Gateway: gateway, AdmitFiles: true}}}
}
func setup(t *testing.T, cfg Config) (*Router, *httptest.Server, *httptest.Server) {
	t.Helper()
	router, err := NewRouter(cfg)
	if err != nil {
		t.Fatal(err)
	}
	public := httptest.NewServer(router.Public())
	t.Cleanup(public.Close)
	admin := httptest.NewServer(router.Admin())
	t.Cleanup(admin.Close)
	return router, public, admin
}
func adminRequest(t *testing.T, admin *httptest.Server, method, path, etag string, value any) *http.Response {
	t.Helper()
	var payload []byte
	if value != nil {
		var err error
		payload, err = json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
	}
	req, err := http.NewRequest(method, admin.URL+path, bytes.NewReader(payload))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("If-Match", etag)
	response, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { response.Body.Close() })
	return response
}
func currentETag(t *testing.T, admin *httptest.Server) string {
	t.Helper()
	response := adminRequest(t, admin, "GET", "/config/", "", nil)
	if response.StatusCode != 200 {
		t.Fatalf("config status %d", response.StatusCode)
	}
	return response.Header.Get("ETag")
}
func switchConfig(t *testing.T, admin *httptest.Server, cfg Config) {
	t.Helper()
	response := adminRequest(t, admin, "POST", "/config/", currentETag(t, admin), cfg)
	if response.StatusCode != 200 {
		data, _ := io.ReadAll(response.Body)
		t.Fatalf("switch status %d: %s", response.StatusCode, data)
	}
}
func counts(t *testing.T, admin *httptest.Server) map[string]int {
	t.Helper()
	response := adminRequest(t, admin, "GET", "/reverse_proxy/upstreams", "", nil)
	var values []struct {
		Address  string `json:"address"`
		Requests int    `json:"num_requests"`
	}
	if err := json.NewDecoder(response.Body).Decode(&values); err != nil {
		t.Fatal(err)
	}
	result := map[string]int{}
	for _, item := range values {
		result[item.Address] = item.Requests
	}
	return result
}
func TestRoutesPreserveHostCredentialsAndTicketIssuer(t *testing.T) {
	old := backend(t, "old")
	replacement := backend(t, "new")
	cfg := config(address(old), address(old))
	cfg.Active = newID
	cfg.Generations[newID] = Generation{API: address(replacement), Gateway: address(replacement), AdmitFiles: true}
	_, public, _ := setup(t, cfg)
	cases := []struct{ path, wantBackend, wantPath string }{
		{"/deedkit/tools?q=1", "new", "/tools?q=1"},
		{"/pos-probe/v1/search", "new", "/v1/search"},
		{"/deedkit/_releases/" + oldID + "/v1/files/" + token, "old", "/v1/files/" + token},
		{"/deedkit/v1/files/" + token, "old", "/v1/files/" + token},
	}
	for _, item := range cases {
		req, _ := http.NewRequest("GET", public.URL+item.path, nil)
		req.Host = "public.example"
		req.Header.Set("Authorization", "Bearer synthetic")
		response, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		var got map[string]string
		err = json.NewDecoder(response.Body).Decode(&got)
		response.Body.Close()
		if err != nil {
			t.Fatal(err)
		}
		if got["backend"] != item.wantBackend || got["path"] != item.wantPath || got["host"] != "public.example" || got["authorization"] != "Bearer synthetic" {
			t.Fatalf("%s: %#v", item.path, got)
		}
	}
	for _, path := range []string{"/status", "/config/", "/unknown", "/deedkit/_releases/ffffffffffff/v1/files/" + token, "/deedkit/_releases/" + oldID + "/v1/files/short", "/deedkit/_releases/" + oldID + "/tools"} {
		response, err := http.Get(public.URL + path)
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		if response.StatusCode != 404 {
			t.Fatalf("%s: %d", path, response.StatusCode)
		}
	}
}
func TestConfigCASRejectsStaleETagWithoutChangingRoutes(t *testing.T) {
	old := backend(t, "old")
	replacement := backend(t, "new")
	cfg := config(address(old), address(old))
	_, _, admin := setup(t, cfg)
	etag := currentETag(t, admin)
	cfg.Generations[newID] = Generation{API: address(replacement), Gateway: address(replacement), AdmitFiles: true}
	cfg.Active = newID
	switchConfig(t, admin, cfg)
	for _, match := range []string{etag, "", "*"} {
		response := adminRequest(t, admin, "POST", "/config/", match, config(address(old), address(old)))
		if response.StatusCode != 412 {
			t.Fatalf("match %q status %d", match, response.StatusCode)
		}
	}
	response := adminRequest(t, admin, "GET", "/config/", "", nil)
	var got Config
	json.NewDecoder(response.Body).Decode(&got)
	if got.Active != newID {
		t.Fatalf("active generation %q", got.Active)
	}
}
func TestRetirementFenceRetainsAdmittedStreamUntilResponseCompletes(t *testing.T) {
	started := make(chan struct{})
	finish := make(chan struct{})
	old := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("first"))
		w.(http.Flusher).Flush()
		close(started)
		<-finish
		w.Write([]byte("last"))
	}))
	defer old.Close()
	replacement := backend(t, "new")
	cfg := config(address(old), address(old))
	_, public, admin := setup(t, cfg)
	req, _ := http.NewRequest("GET", public.URL+"/deedkit/_releases/"+oldID+"/v1/files/"+token, nil)
	response, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		close(finish)
		t.Fatalf("stream status %d", response.StatusCode)
	}
	<-started
	if got := counts(t, admin)[address(old)]; got != 1 {
		t.Fatalf("admitted stream census %d", got)
	}
	cfg.Active = newID
	cfg.Generations[newID] = Generation{API: address(replacement), Gateway: address(replacement), AdmitFiles: true}
	switchConfig(t, admin, cfg)
	fenced := adminRequest(t, admin, "POST", "/generations/"+oldID+"/retire", currentETag(t, admin), map[string]any{})
	if fenced.StatusCode != 200 {
		t.Fatalf("fence status %d", fenced.StatusCode)
	}
	rejected, err := http.Get(req.URL.String())
	if err != nil {
		t.Fatal(err)
	}
	rejected.Body.Close()
	if rejected.StatusCode != 410 {
		t.Fatalf("fenced admission %d", rejected.StatusCode)
	}
	if got := counts(t, admin)[address(old)]; got != 1 {
		t.Fatalf("fence lost active lease %d", got)
	}
	close(finish)
	data, err := io.ReadAll(response.Body)
	if err != nil || string(data) != "firstlast" {
		t.Fatalf("stream %q %v", data, err)
	}
	deadline := time.Now().Add(time.Second)
	for counts(t, admin)[address(old)] != 0 {
		if time.Now().After(deadline) {
			t.Fatal("lease not released")
		}
		time.Sleep(time.Millisecond)
	}
	cfg.Generations[oldID] = Generation{API: address(old), Gateway: address(old), AdmitFiles: true}
	if got := adminRequest(t, admin, "POST", "/config/", currentETag(t, admin), cfg).StatusCode; got != 409 {
		t.Fatalf("reopened fenced issuer %d", got)
	}
	if got := adminRequest(t, admin, "POST", "/generations/"+newID+"/retire", currentETag(t, admin), map[string]any{}).StatusCode; got != 409 {
		t.Fatalf("retired active generation %d", got)
	}
}
func TestSlowHeadersSelectGenerationAfterCAS(t *testing.T) {
	old := backend(t, "old")
	replacement := backend(t, "new")
	cfg := config(address(old), address(old))
	_, public, admin := setup(t, cfg)
	location, _ := url.Parse(public.URL)
	conn, err := net.Dial("tcp", location.Host)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(3 * time.Second))
	fmt.Fprintf(conn, "GET /deedkit/tools HTTP/1.1\r\nHost: synthetic\r\nX-Partial: ")
	cfg.Active = newID
	cfg.Generations[newID] = Generation{API: address(replacement), Gateway: address(replacement), AdmitFiles: true}
	switchConfig(t, admin, cfg)
	io.WriteString(conn, "complete\r\nConnection: close\r\n\r\n")
	data, err := io.ReadAll(conn)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(data), `"backend":"new"`) {
		t.Fatalf("slow headers selected retired generation: %s", data)
	}
}

func TestLeaseIsAcquiredBeforeDialAndBlocksRemoval(t *testing.T) {
	old := backend(t, "old")
	replacement := backend(t, "new")
	cfg := config(address(old), address(old))
	router, public, admin := setup(t, cfg)
	dialing := make(chan struct{})
	proceed := make(chan struct{})
	originalDial := router.transport.DialContext
	router.transport.DialContext = func(ctx context.Context, network, address string) (net.Conn, error) {
		close(dialing)
		select {
		case <-proceed:
			return originalDial(ctx, network, address)
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
	result := make(chan *http.Response, 1)
	go func() { response, _ := http.Get(public.URL + "/deedkit/tools"); result <- response }()
	<-dialing
	if got := counts(t, admin)[address(old)]; got != 1 {
		close(proceed)
		t.Fatalf("pre-dial lease %d", got)
	}
	cfg.Active = newID
	cfg.Generations[newID] = Generation{API: address(replacement), Gateway: address(replacement), AdmitFiles: true}
	switchConfig(t, admin, cfg)
	fence := adminRequest(t, admin, "POST", "/generations/"+oldID+"/retire", currentETag(t, admin), map[string]any{})
	if fence.StatusCode != 200 {
		close(proceed)
		t.Fatalf("fence %d", fence.StatusCode)
	}
	delete(cfg.Generations, oldID)
	if got := adminRequest(t, admin, "POST", "/config/", currentETag(t, admin), cfg).StatusCode; got != 409 {
		close(proceed)
		t.Fatalf("removed leased generation %d", got)
	}
	close(proceed)
	response := <-result
	if response == nil {
		t.Fatal("proxied request failed")
	}
	data, _ := io.ReadAll(response.Body)
	response.Body.Close()
	if !strings.Contains(string(data), `"backend":"old"`) {
		t.Fatalf("selected request switched backend: %s", data)
	}
}
func TestPrivateAdminRequiresStrictBoundedObjectConfiguration(t *testing.T) {
	old := backend(t, "old")
	cfg := config(address(old), address(old))
	_, _, admin := setup(t, cfg)
	encoded, _ := json.Marshal(cfg)
	invalid := []string{
		strings.Replace(string(encoded), `"version":1`, `"version":1,"unknown":true`, 1),
		strings.Replace(string(encoded), `"version":1`, `"version":1,"version":1`, 1),
		string(encoded) + ` {}`,
		strings.Repeat(" ", maxConfigBytes) + string(encoded),
		strings.Replace(string(encoded), `"admit_files":true`, `"unexpected":true`, 1),
		strings.Replace(string(encoded), `"active":"`+oldID+`"`, `"active":""`, 1),
	}
	for _, body := range invalid {
		req, _ := http.NewRequest("POST", admin.URL+"/config/", strings.NewReader(body))
		req.Header.Set("If-Match", currentETag(t, admin))
		response, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		if response.StatusCode != 400 {
			t.Fatalf("accepted invalid object: status %d", response.StatusCode)
		}
	}
	for _, body := range []string{`null`, `{"unknown":true}`} {
		req, _ := http.NewRequest("POST", admin.URL+"/generations/"+oldID+"/retire", strings.NewReader(body))
		req.Header.Set("If-Match", currentETag(t, admin))
		response, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		if response.StatusCode != 400 {
			t.Fatalf("retire body %s status %d", body, response.StatusCode)
		}
	}
	response := adminRequest(t, admin, "GET", "/status", "", nil)
	var status map[string]any
	json.NewDecoder(response.Body).Decode(&status)
	if status["kind"] != "deedkit-release-router" || status["protocol"] != float64(1) {
		t.Fatalf("untrusted identity: %#v", status)
	}
}
func TestUnixAdminSocketIsPrivateAndNeverReplacesExistingEntry(t *testing.T) {
	directory, err := os.MkdirTemp("/tmp", "dk-router-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(directory) })
	if err := os.Chmod(directory, 0700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "admin.sock")
	listener, err := privateListener(path)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Release()
	info, err := os.Lstat(path)
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatalf("socket permissions %v %v", info, err)
	}
	if other, err := privateListener(path); err == nil {
		other.Release()
		t.Fatal("replaced an existing socket")
	}
	listener.Close()
	os.Chmod(directory, 0755)
	if other, err := privateListener(path); err == nil {
		other.Release()
		t.Fatal("accepted a public parent directory")
	}
	os.Chmod(directory, 0700)
	link := filepath.Join(directory, "link")
	if err := os.Symlink(directory, link); err != nil {
		t.Fatal(err)
	}
	if other, err := privateListener(filepath.Join(link, "admin.sock")); err == nil {
		other.Release()
		t.Fatal("accepted symlink parent")
	}
}

func TestUnixAdminRecoversOnlyStaleOwnedSockets(t *testing.T) {
	directory, err := os.MkdirTemp("/tmp", "dk-stale-")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(directory)
	path := filepath.Join(directory, "admin.sock")
	old, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	old.SetUnlinkOnClose(false)
	os.Chmod(path, 0600)
	old.Close()
	recovered, err := privateListener(path)
	if err != nil {
		t.Fatalf("stale socket did not recover: %v", err)
	}
	recovered.Release()
	live, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	os.Chmod(path, 0600)
	if other, err := privateListener(path); err == nil {
		other.Release()
		live.Close()
		t.Fatal("unlinked a live foreign listener")
	}
	live.Close()
	if err := os.WriteFile(path, []byte("private configuration"), 0600); err != nil {
		t.Fatal(err)
	}
	if other, err := privateListener(path); err == nil {
		other.Release()
		t.Fatal("replaced a regular file")
	}
	if data, _ := os.ReadFile(path); string(data) != "private configuration" {
		t.Fatal("changed private regular file")
	}
	os.Remove(path)
	os.Remove(path + ".lock")
	if err := os.Symlink(filepath.Join(directory, "target"), path+".lock"); err != nil {
		t.Fatal(err)
	}
	if other, err := privateListener(path); err == nil {
		other.Release()
		t.Fatal("accepted symlink lock")
	}
}

func TestRetirementFenceCannotBeForgottenByDeletingGeneration(t *testing.T) {
	old := backend(t, "old")
	replacement := backend(t, "new")
	cfg := config(address(old), address(old))
	_, _, admin := setup(t, cfg)
	cfg.Active = newID
	cfg.Generations[newID] = Generation{API: address(replacement), Gateway: address(replacement), AdmitFiles: true}
	switchConfig(t, admin, cfg)
	response := adminRequest(t, admin, "POST", "/generations/"+oldID+"/retire", currentETag(t, admin), map[string]any{})
	if response.StatusCode != 200 {
		t.Fatalf("fence %d", response.StatusCode)
	}
	delete(cfg.Generations, oldID)
	response = adminRequest(t, admin, "POST", "/config/", currentETag(t, admin), cfg)
	if response.StatusCode != 409 {
		t.Fatalf("forgot irreversible fence: %d", response.StatusCode)
	}
}
func TestCancelledResponseReleasesLeaseAfterBackendCancellation(t *testing.T) {
	cancelled := make(chan struct{})
	old := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("first"))
		w.(http.Flusher).Flush()
		<-r.Context().Done()
		close(cancelled)
	}))
	defer old.Close()
	_, public, admin := setup(t, config(address(old), address(old)))
	response, err := http.Get(public.URL + "/deedkit/v1/files/" + token)
	if err != nil {
		t.Fatal(err)
	}
	if got := counts(t, admin)[address(old)]; got != 1 {
		response.Body.Close()
		t.Fatalf("active stream %d", got)
	}
	response.Body.Close()
	select {
	case <-cancelled:
	case <-time.After(3 * time.Second):
		t.Fatal("backend did not receive cancellation")
	}
	deadline := time.Now().Add(time.Second)
	for counts(t, admin)[address(old)] != 0 {
		if time.Now().After(deadline) {
			t.Fatal("cancelled stream retained lease")
		}
		time.Sleep(time.Millisecond)
	}
}

func TestAdminShutdownKeepsProcessLockUntilPublicStreamDrains(t *testing.T) {
	directory, err := os.MkdirTemp("/tmp", "dk-drain-")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(directory)
	path := filepath.Join(directory, "admin.sock")
	listener, err := privateListener(path)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Release()
	router, err := NewRouter(config("synthetic-api:8080", "synthetic-gateway:13240"))
	if err != nil {
		t.Fatal(err)
	}
	finish := make(chan struct{})
	var finishOnce sync.Once
	releaseStream := func() { finishOnce.Do(func() { close(finish) }) }
	old := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("first"))
		w.(http.Flusher).Flush()
		<-finish
		w.Write([]byte("last"))
	}))
	defer old.Close()
	router.config.Legacy.Gateway = address(old)
	item := router.config.Generations[oldID]
	item.Gateway = address(old)
	router.config.Generations[oldID] = item
	public := httptest.NewServer(router.Public())
	defer public.Close()
	defer releaseStream()
	admin := &http.Server{Handler: router.Admin()}
	go admin.Serve(listener)
	defer admin.Close()
	response, err := http.Get(public.URL + "/deedkit/v1/files/" + token)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	publicStopped := make(chan error, 1)
	go func() { err := public.Config.Shutdown(context.Background()); router.waitIdle(); publicStopped <- err }()
	if err := admin.Shutdown(context.Background()); err != nil {
		t.Fatal(err)
	}
	if competitor, err := privateListener(path); err == nil {
		competitor.Release()
		t.Fatal("replacement router acquired process lock while old public stream was draining")
	}
	select {
	case <-publicStopped:
		t.Fatal("public shutdown completed with admitted stream")
	default:
	}
	releaseStream()
	data, err := io.ReadAll(response.Body)
	if err != nil || string(data) != "firstlast" {
		t.Fatalf("stream did not finish: %q %v", data, err)
	}
	select {
	case err := <-publicStopped:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("public shutdown did not complete")
	}
	if err := listener.Release(); err != nil && !errors.Is(err, net.ErrClosed) {
		t.Fatal(err)
	}
	competitor, err := privateListener(path)
	if err != nil {
		t.Fatalf("process lock not released after drain: %v", err)
	}
	competitor.Release()
}

func TestOwnershipWaitIncludesUpgradedProxyLeaseAfterHTTPShutdown(t *testing.T) {
	connections := make(chan net.Conn, 1)
	old := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		connection, buffer, err := w.(http.Hijacker).Hijack()
		if err != nil {
			return
		}
		defer connection.Close()
		fmt.Fprint(buffer, "HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: synthetic\r\n\r\n")
		buffer.Flush()
		connections <- connection
		io.Copy(connection, connection)
	}))
	defer old.Close()
	router, public, _ := setup(t, config(address(old), address(old)))
	req, _ := http.NewRequest("GET", public.URL+"/deedkit/stream", nil)
	req.Header.Set("Connection", "Upgrade")
	req.Header.Set("Upgrade", "synthetic")
	response, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != 101 {
		t.Fatalf("upgrade status %d", response.StatusCode)
	}
	connection := <-connections
	defer connection.Close()
	if err := public.Config.Shutdown(context.Background()); err != nil {
		t.Fatal(err)
	}
	ownershipDone := make(chan struct{})
	go func() { router.waitIdle(); close(ownershipDone) }()
	select {
	case <-ownershipDone:
		t.Fatal("ownership transferred while upgraded proxy lease remained")
	case <-time.After(30 * time.Millisecond):
	}
	response.Body.Close()
	select {
	case <-ownershipDone:
	case <-time.After(3 * time.Second):
		t.Fatal("completed upgraded proxy did not release ownership")
	}
}
