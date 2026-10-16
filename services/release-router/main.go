package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"sync"
	"syscall"
	"time"
)

func privateParent(path string) error {
	if !filepath.IsAbs(path) || filepath.Clean(path) != path {
		return errors.New("private path must be absolute and canonical")
	}
	info, err := os.Lstat(filepath.Dir(path))
	if err != nil {
		return err
	}
	owner, ok := info.Sys().(*syscall.Stat_t)
	if !info.IsDir() || info.Mode().Perm() != 0700 || !ok || owner.Uid != uint32(os.Geteuid()) {
		return errors.New("private parent must be owned by the process and mode 0700")
	}
	return nil
}

type lockedListener struct {
	net.Listener
	lock        *os.File
	once        sync.Once
	releaseOnce sync.Once
	closeError  error
}

func (l *lockedListener) Close() error {
	l.once.Do(func() {
		l.closeError = l.Listener.Close()
	})
	return l.closeError
}
func (l *lockedListener) Release() error {
	// Closing the admin listener must not transfer process ownership while the
	// public server still drains. Only run's final cleanup releases the lock.
	err := l.Close()
	l.releaseOnce.Do(func() { syscall.Flock(int(l.lock.Fd()), syscall.LOCK_UN); l.lock.Close() })
	return err
}
func privateListener(path string) (*lockedListener, error) {
	if err := privateParent(path); err != nil {
		return nil, err
	}
	lock, err := os.OpenFile(path+".lock", os.O_CREATE|os.O_RDWR|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return nil, err
	}
	keepLock := false
	defer func() {
		if !keepLock {
			lock.Close()
		}
	}()
	info, err := lock.Stat()
	if err != nil {
		return nil, err
	}
	owner, ok := info.Sys().(*syscall.Stat_t)
	if !info.Mode().IsRegular() || info.Mode().Perm() != 0600 || !ok || owner.Uid != uint32(os.Geteuid()) {
		return nil, errors.New("invalid private socket lock")
	}
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		return nil, errors.New("private socket is already owned")
	}
	if info, err := os.Lstat(path); err == nil {
		owner, ok := info.Sys().(*syscall.Stat_t)
		if info.Mode()&os.ModeSocket == 0 || info.Mode().Perm() != 0600 || !ok || owner.Uid != uint32(os.Geteuid()) {
			return nil, errors.New("refusing to replace a foreign admin entry")
		}
		// Holding the process-lifetime lock excludes our live instances. A foreign
		// live listener is also preserved. Only a refused, owned Unix socket is stale.
		connection, dialError := net.DialTimeout("unix", path, 250*time.Millisecond)
		if dialError == nil {
			connection.Close()
			return nil, errors.New("private socket has a live listener")
		}
		if !errors.Is(dialError, syscall.ECONNREFUSED) {
			return nil, errors.New("cannot prove the private socket is stale")
		}
		if err := os.Remove(path); err != nil {
			return nil, err
		}
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	listener, err := net.Listen("unix", path)
	if err != nil {
		return nil, err
	}
	if err := os.Chmod(path, 0600); err != nil {
		listener.Close()
		return nil, err
	}
	keepLock = true
	return &lockedListener{Listener: listener, lock: lock}, nil
}
func loadConfig(path string) (Config, error) {
	var config Config
	if err := privateParent(path); err != nil {
		return config, err
	}
	info, err := os.Lstat(path)
	if err != nil {
		return config, err
	}
	owner, ok := info.Sys().(*syscall.Stat_t)
	if !info.Mode().IsRegular() || info.Mode().Perm() != 0600 || !ok || owner.Uid != uint32(os.Geteuid()) {
		return config, errors.New("configuration must be a private process-owned regular file")
	}
	file, err := os.Open(path)
	if err != nil {
		return config, err
	}
	defer file.Close()
	if err := decodeStrict(file, &config); err != nil {
		return config, err
	}
	if config.AdminSocket != filepath.Join(filepath.Dir(path), "admin.sock") {
		return config, errors.New("admin socket must share the configuration parent")
	}
	return config, validateConfig(config)
}
func run(configPath string) error {
	config, err := loadConfig(configPath)
	if err != nil {
		return err
	}
	router, err := NewRouter(config)
	if err != nil {
		return err
	}
	adminListener, err := privateListener(config.AdminSocket)
	if err != nil {
		return err
	}
	defer adminListener.Release()
	publicListener, err := net.Listen("tcp", config.Listen)
	if err != nil {
		return err
	}
	defer publicListener.Close()
	public := &http.Server{Handler: router.Public(), ReadHeaderTimeout: 15 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 65536}
	admin := &http.Server{Handler: router.Admin(), ReadHeaderTimeout: 3 * time.Second, ReadTimeout: 3 * time.Second, WriteTimeout: 3 * time.Second, IdleTimeout: 3 * time.Second, MaxHeaderBytes: 8192}
	results := make(chan error, 2)
	go func() { results <- public.Serve(publicListener) }()
	go func() { results <- admin.Serve(adminListener) }()
	stopping, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	select {
	case <-stopping.Done():
	case err = <-results:
	}
	// No guessed grace timeout may interrupt an admitted upload or download.
	// Stop admission first, then wait for every proxy handler to release its lease.
	publicShutdown := make(chan error, 1)
	go func() { publicShutdown <- public.Shutdown(context.Background()) }()
	adminErr := admin.Shutdown(context.Background())
	publicErr := <-publicShutdown
	router.waitIdle()
	router.transport.CloseIdleConnections()
	if err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	if adminErr != nil {
		return adminErr
	}
	return publicErr
}
func main() {
	configPath := flag.String("config", "/run/deedkit-router/router.json", "private reviewed router configuration")
	version := flag.Bool("version", false, "print private-protocol identity")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "unexpected arguments")
		os.Exit(2)
	}
	if *version {
		json.NewEncoder(os.Stdout).Encode(map[string]any{"protocol": protocol, "kind": kind})
		return
	}
	if err := run(*configPath); err != nil {
		fmt.Fprintln(os.Stderr, "release router failed:", err)
		os.Exit(1)
	}
}
