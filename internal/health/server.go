package health

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"github.com/magicorn/epictetus/internal/cloudflare"
	"github.com/magicorn/epictetus/internal/controller"
)

type Server struct {
	port       int
	cfClient   *cloudflare.Client
	reconciler *controller.Reconciler
}

func NewServer(port int, cfClient *cloudflare.Client, reconciler *controller.Reconciler) *Server {
	return &Server{port: port, cfClient: cfClient, reconciler: reconciler}
}

// Start runs the health HTTP server and shuts it down gracefully when ctx is cancelled.
func (s *Server) Start(ctx context.Context) {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", s.handleHealth)
	mux.HandleFunc("/health/live", s.handleLive)
	mux.HandleFunc("/health/ready", s.handleReady)

	srv := &http.Server{
		Addr:         fmt.Sprintf(":%d", s.port),
		Handler:      mux,
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 10 * time.Second,
	}

	go func() {
		<-ctx.Done()
		shutCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := srv.Shutdown(shutCtx); err != nil {
			slog.Error("health server shutdown error", "err", err)
		}
	}()

	slog.Info("health server listening", "addr", srv.Addr)
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		slog.Error("health server stopped unexpectedly", "err", err)
	}
}

func (s *Server) handleLive(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{
		"status":    "alive",
		"timestamp": time.Now().Format(time.RFC3339),
	})
}

func (s *Server) handleReady(w http.ResponseWriter, r *http.Request) {
	if s.reconciler.Ready() {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ready"})
	} else {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{
			"status": "not_ready",
			"reason": "initial sync pending",
		})
	}
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	cfErr := s.cfClient.HealthCheck(ctx)
	zones := s.cfClient.AvailableZones()

	cfStatus := "healthy"
	if cfErr != nil {
		cfStatus = "unhealthy"
	}

	overall := "healthy"
	code := http.StatusOK
	if cfErr != nil {
		overall = "unhealthy"
		code = http.StatusServiceUnavailable
	}

	body := map[string]interface{}{
		"status":    overall,
		"timestamp": time.Now().Format(time.RFC3339),
		"cloudflare": map[string]interface{}{
			"status": cfStatus,
			"zones":  zones,
		},
	}
	if cfErr != nil {
		body["error"] = cfErr.Error()
	}

	writeJSON(w, code, body)
}

func writeJSON(w http.ResponseWriter, code int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		slog.Error("failed to write health response", "err", err)
	}
}
