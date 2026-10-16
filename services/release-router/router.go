// The release router keeps one HTTP listener for its entire lifetime. Selection,
// lease acquisition, configuration CAS and issuer fencing share the same mutex.
package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
)

const protocol = 1
const kind = "deedkit-release-router"
const maxConfigBytes = 1 << 20

var generationPattern = regexp.MustCompile(`^[0-9a-f]{12}$`)
var tokenPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{43}$`)
var hostPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]*$`)

type Targets struct {
	API     string `json:"api"`
	Gateway string `json:"gateway"`
}
type Generation struct {
	API        string `json:"api"`
	Gateway    string `json:"gateway"`
	AdmitFiles bool   `json:"admit_files"`
}
type Config struct {
	Version     int                   `json:"version"`
	Listen      string                `json:"listen"`
	AdminSocket string                `json:"admin_socket"`
	Active      string                `json:"active"`
	Legacy      Targets               `json:"legacy"`
	Generations map[string]Generation `json:"generations"`
}

func (c Config) MarshalJSON() ([]byte, error) {
	type plain Config
	var active any
	if c.Active != "" {
		active = c.Active
	}
	return json.Marshal(struct {
		plain
		Active any `json:"active"`
	}{plain(c), active})
}
func (c *Config) UnmarshalJSON(data []byte) error {
	type plain Config
	var decoded plain
	decoder := json.NewDecoder(strings.NewReader(string(data)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&decoded); err != nil {
		return err
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		return err
	}
	if len(fields) != 6 {
		return errors.New("all configuration fields are required")
	}
	active, exists := fields["active"]
	if !exists || (string(active) != "null" && !generationPattern.MatchString(decoded.Active)) {
		return errors.New("active must be null or a generation")
	}
	*c = Config(decoded)
	return nil
}
func (g *Generation) UnmarshalJSON(data []byte) error {
	type plain Generation
	var decoded plain
	decoder := json.NewDecoder(strings.NewReader(string(data)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&decoded); err != nil {
		return err
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		return err
	}
	if len(fields) != 3 || string(fields["admit_files"]) == "null" {
		return errors.New("all generation fields are required")
	}
	*g = Generation(decoded)
	return nil
}
func validAddress(value string) bool {
	host, port, err := net.SplitHostPort(value)
	number, numberErr := strconv.Atoi(port)
	return err == nil && hostPattern.MatchString(host) && numberErr == nil && number >= 1 && number <= 65535 && port == strconv.Itoa(number)
}
func validateConfig(c Config) error {
	if c.Version != 1 || c.Listen != ":8080" || c.AdminSocket == "" || !strings.HasPrefix(c.AdminSocket, "/") || !strings.HasSuffix(c.AdminSocket, "/admin.sock") {
		return errors.New("invalid router version, listener or private socket")
	}
	if !validAddress(c.Legacy.API) || !validAddress(c.Legacy.Gateway) || c.Generations == nil || len(c.Generations) > 4096 {
		return errors.New("invalid targets or generation inventory")
	}
	for id, item := range c.Generations {
		if !generationPattern.MatchString(id) || !validAddress(item.API) || !validAddress(item.Gateway) {
			return errors.New("invalid generation target")
		}
	}
	if c.Active != "" {
		item, found := c.Generations[c.Active]
		if !found || !item.AdmitFiles {
			return errors.New("active generation is absent or fenced")
		}
	}
	return nil
}
func cloneConfig(c Config) Config {
	result := c
	result.Generations = make(map[string]Generation, len(c.Generations))
	for id, item := range c.Generations {
		result.Generations[id] = item
	}
	return result
}

type Router struct {
	mu        sync.Mutex
	idle      *sync.Cond
	config    Config
	leases    map[string]int
	nonce     string
	revision  uint64
	transport *http.Transport
}

func NewRouter(config Config) (*Router, error) {
	if err := validateConfig(config); err != nil {
		return nil, err
	}
	nonce := make([]byte, 16)
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil // Internal aliases must never follow an environment proxy.
	transport.ResponseHeaderTimeout = 0
	transport.MaxIdleConnsPerHost = 16
	router := &Router{config: cloneConfig(config), leases: make(map[string]int), nonce: hex.EncodeToString(nonce), transport: transport}
	router.idle = sync.NewCond(&router.mu)
	return router, nil
}

// Wait for all selected proxy handlers, including upgraded connections which
// net/http.Server.Shutdown does not wait for, before transferring router ownership.
func (r *Router) waitIdle() {
	r.mu.Lock()
	defer r.mu.Unlock()
	for {
		idle := true
		for _, count := range r.leases {
			if count != 0 {
				idle = false
				break
			}
		}
		if idle {
			return
		}
		r.idle.Wait()
	}
}
func (r *Router) etagLocked() string   { return fmt.Sprintf(`"%s-%d"`, r.nonce, r.revision) }
func (r *Router) Public() http.Handler { return http.HandlerFunc(r.servePublic) }
func (r *Router) Admin() http.Handler  { return http.HandlerFunc(r.serveAdmin) }

// Called only with mu held. The HTTP server has already parsed the complete
// request headers; even a connection accepted before CAS selects current state.
func (r *Router) selectLocked(request *http.Request) (string, string, int) {
	path := request.URL.Path
	var targets Targets
	if r.config.Active == "" {
		targets = r.config.Legacy
	} else {
		active := r.config.Generations[r.config.Active]
		targets = Targets{active.API, active.Gateway}
	}
	if strings.HasPrefix(path, "/deedkit/_releases/") {
		rest := strings.TrimPrefix(path, "/deedkit/_releases/")
		parts := strings.Split(rest, "/")
		if len(parts) != 4 || !generationPattern.MatchString(parts[0]) || parts[1] != "v1" || parts[2] != "files" || !tokenPattern.MatchString(parts[3]) {
			return "", "", 404
		}
		item, found := r.config.Generations[parts[0]]
		if !found {
			return "", "", 404
		}
		if !item.AdmitFiles {
			return "", "", 410
		}
		return item.Gateway, "/v1/files/" + parts[3], 0
	}
	if strings.HasPrefix(path, "/deedkit/v1/files/") {
		fileToken := strings.TrimPrefix(path, "/deedkit/v1/files/")
		if !tokenPattern.MatchString(fileToken) {
			return "", "", 404
		}
		return r.config.Legacy.Gateway, strings.TrimPrefix(path, "/deedkit"), 0
	}
	if strings.HasPrefix(path, "/deedkit/") {
		return targets.Gateway, strings.TrimPrefix(path, "/deedkit"), 0
	}
	if strings.HasPrefix(path, "/pos-probe/") {
		return targets.API, strings.TrimPrefix(path, "/pos-probe"), 0
	}
	return "", "", 404
}
func (r *Router) servePublic(w http.ResponseWriter, request *http.Request) {
	// Avoid accepting an encoded path with a different issuer/prefix interpretation
	// in another HTTP layer. File URLs use only literal base64url characters.
	escaped := strings.ToLower(request.URL.EscapedPath())
	if strings.Contains(escaped, "%2f") || strings.Contains(escaped, "%5c") {
		http.NotFound(w, request)
		return
	}
	r.mu.Lock()
	address, path, status := r.selectLocked(request)
	if status == 0 {
		r.leases[address]++
	}
	r.mu.Unlock()
	if status != 0 {
		http.Error(w, http.StatusText(status), status)
		return
	}
	defer func() { r.mu.Lock(); r.leases[address]--; r.idle.Broadcast(); r.mu.Unlock() }()
	target := &url.URL{Scheme: "http", Host: address}
	proxy := &httputil.ReverseProxy{
		Transport:     r.transport,
		FlushInterval: -1,
		ErrorLog:      log.New(io.Discard, "", 0),
		ErrorHandler: func(w http.ResponseWriter, _ *http.Request, _ error) {
			http.Error(w, "upstream unavailable", http.StatusBadGateway)
		},
		Rewrite: func(proxyRequest *httputil.ProxyRequest) {
			proxyRequest.SetURL(target)
			proxyRequest.Out.URL.Path = path
			proxyRequest.Out.URL.RawPath = ""
			proxyRequest.Out.Host = proxyRequest.In.Host
			// The public ingress owns forwarding headers; retain its existing values.
			for _, header := range []string{"Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto"} {
				proxyRequest.Out.Header[header] = append([]string(nil), proxyRequest.In.Header.Values(header)...)
			}
		},
	}
	proxy.ServeHTTP(w, request)
}
func jsonResponse(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(value)
}
func (r *Router) responseConfigLocked(w http.ResponseWriter) {
	w.Header().Set("ETag", r.etagLocked())
	jsonResponse(w, 200, r.config)
}
func (r *Router) transitionLocked(candidate Config) error {
	if candidate.Listen != r.config.Listen || candidate.AdminSocket != r.config.AdminSocket || candidate.Legacy != r.config.Legacy {
		return errors.New("immutable router topology changed")
	}
	for id, previous := range r.config.Generations {
		next, exists := candidate.Generations[id]
		if !exists {
			// Persist every issuer tombstone. Removing then re-adding an ID must
			// never reopen an irreversible admission fence, even after restart.
			return errors.New("generation identities cannot be removed")
		}
		if previous.API != next.API || previous.Gateway != next.Gateway || (!previous.AdmitFiles && next.AdmitFiles) {
			return errors.New("generation identity or fence changed")
		}
	}
	return nil
}
func (r *Router) serveAdmin(w http.ResponseWriter, request *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	switch {
	case request.Method == "GET" && request.URL.Path == "/status":
		jsonResponse(w, 200, map[string]any{"protocol": protocol, "kind": kind})
		return
	case request.Method == "GET" && request.URL.Path == "/config/":
		r.mu.Lock()
		defer r.mu.Unlock()
		r.responseConfigLocked(w)
		return
	case request.Method == "GET" && request.URL.Path == "/reverse_proxy/upstreams":
		r.mu.Lock()
		defer r.mu.Unlock()
		addresses := map[string]bool{r.config.Legacy.API: true, r.config.Legacy.Gateway: true}
		for _, item := range r.config.Generations {
			addresses[item.API] = true
			addresses[item.Gateway] = true
		}
		names := make([]string, 0, len(addresses))
		for address := range addresses {
			names = append(names, address)
		}
		sort.Strings(names)
		values := make([]map[string]any, 0, len(names))
		for _, address := range names {
			values = append(values, map[string]any{"address": address, "num_requests": r.leases[address]})
		}
		jsonResponse(w, 200, values)
		return
	case request.Method == "POST" && request.URL.Path == "/config/":
		var candidate Config
		if err := decodeStrict(request.Body, &candidate); err != nil {
			http.Error(w, "invalid configuration", 400)
			return
		}
		if err := validateConfig(candidate); err != nil {
			http.Error(w, "invalid configuration", 400)
			return
		}
		r.mu.Lock()
		defer r.mu.Unlock()
		if request.Header.Get("If-Match") != r.etagLocked() {
			http.Error(w, "configuration changed", 412)
			return
		}
		if err := r.transitionLocked(candidate); err != nil {
			http.Error(w, "unsafe configuration transition", 409)
			return
		}
		r.config = cloneConfig(candidate)
		r.revision++
		r.responseConfigLocked(w)
		return
	case request.Method == "POST" && strings.HasPrefix(request.URL.Path, "/generations/") && strings.HasSuffix(request.URL.Path, "/retire"):
		id := strings.TrimSuffix(strings.TrimPrefix(request.URL.Path, "/generations/"), "/retire")
		if !generationPattern.MatchString(id) {
			http.NotFound(w, request)
			return
		}
		var body struct{}
		if err := decodeStrict(request.Body, &body); err != nil {
			http.Error(w, "invalid retirement request", 400)
			return
		}
		r.mu.Lock()
		defer r.mu.Unlock()
		if request.Header.Get("If-Match") != r.etagLocked() {
			http.Error(w, "configuration changed", 412)
			return
		}
		item, found := r.config.Generations[id]
		if !found {
			http.NotFound(w, request)
			return
		}
		if r.config.Active == id {
			http.Error(w, "active generation cannot retire", 409)
			return
		}
		item.AdmitFiles = false
		r.config.Generations[id] = item
		r.revision++
		r.responseConfigLocked(w)
		return
	default:
		http.NotFound(w, request)
	}
}

// Reject unknown keys, duplicate keys, trailing JSON and oversized documents.
func decodeStrict(reader io.Reader, destination any) error {
	data, err := io.ReadAll(io.LimitReader(reader, maxConfigBytes+1))
	if err != nil {
		return err
	}
	if len(data) > maxConfigBytes {
		return errors.New("document too large")
	}
	if trimmed := strings.TrimSpace(string(data)); len(trimmed) == 0 || trimmed[0] != '{' {
		return errors.New("JSON object required")
	}
	check := json.NewDecoder(strings.NewReader(string(data)))
	if err := uniqueJSON(check); err != nil {
		return err
	}
	if _, err := check.Token(); err != io.EOF {
		return errors.New("trailing JSON")
	}
	decoder := json.NewDecoder(strings.NewReader(string(data)))
	decoder.DisallowUnknownFields()
	return decoder.Decode(destination)
}
func uniqueJSON(decoder *json.Decoder) error {
	token, err := decoder.Token()
	if err != nil {
		return err
	}
	delimiter, ok := token.(json.Delim)
	if !ok {
		return nil
	}
	switch delimiter {
	case '{':
		keys := map[string]bool{}
		for decoder.More() {
			keyToken, err := decoder.Token()
			if err != nil {
				return err
			}
			key, ok := keyToken.(string)
			if !ok || keys[key] {
				return errors.New("duplicate JSON key")
			}
			keys[key] = true
			if err := uniqueJSON(decoder); err != nil {
				return err
			}
		}
	case '[':
		for decoder.More() {
			if err := uniqueJSON(decoder); err != nil {
				return err
			}
		}
	default:
		return errors.New("unexpected JSON delimiter")
	}
	_, err = decoder.Token()
	return err
}
