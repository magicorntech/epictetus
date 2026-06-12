package main

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/magicorn/epictetus/internal/cloudflare"
	"github.com/magicorn/epictetus/internal/config"
	"github.com/magicorn/epictetus/internal/controller"
	"github.com/magicorn/epictetus/internal/health"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
)

func main() {
	if err := run(); err != nil {
		slog.Error("fatal", "err", err)
		os.Exit(1)
	}
}

func run() error {
	cfg, err := config.Load()
	if err != nil {
		return fmt.Errorf("config: %w", err)
	}

	setupLogger(cfg)

	slog.Info("starting epictetus — a poor man's load balancer",
		"sync_interval", cfg.SyncInterval,
		"workers", cfg.Workers,
		"health_port", cfg.HealthPort,
		"log_level", cfg.LogLevel,
	)

	k8sCfg, err := buildK8sConfig(cfg.KubeconfigPath)
	if err != nil {
		return fmt.Errorf("k8s config: %w", err)
	}
	k8sClient, err := kubernetes.NewForConfig(k8sCfg)
	if err != nil {
		return fmt.Errorf("k8s client: %w", err)
	}

	cfClient, err := cloudflare.NewClient(cfg.CloudflareAPIToken, cfg.CFMaxConcurrency)
	if err != nil {
		return fmt.Errorf("cloudflare client: %w", err)
	}
	slog.Info("cloudflare connected", "zones", cfClient.AvailableZones())

	store := controller.NewServiceStore()
	reconciler := controller.NewReconciler(cfClient, store, cfg)
	ctrl := controller.New(k8sClient, reconciler, store, time.Duration(cfg.SyncInterval)*time.Second)

	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()

	healthSrv := health.NewServer(cfg.HealthPort, cfClient, reconciler)
	go healthSrv.Start(ctx)

	return ctrl.Run(ctx, cfg.Workers)
}

func buildK8sConfig(kubeconfigPath string) (*rest.Config, error) {
	if kubeconfigPath != "" {
		return clientcmd.BuildConfigFromFlags("", kubeconfigPath)
	}
	return rest.InClusterConfig()
}

func setupLogger(cfg *config.Config) {
	level := slog.LevelInfo
	switch cfg.LogLevel {
	case "DEBUG":
		level = slog.LevelDebug
	case "WARN", "WARNING":
		level = slog.LevelWarn
	case "ERROR":
		level = slog.LevelError
	}
	opts := &slog.HandlerOptions{Level: level}
	var h slog.Handler
	if cfg.LogFormat == "json" {
		h = slog.NewJSONHandler(os.Stdout, opts)
	} else {
		h = slog.NewTextHandler(os.Stdout, opts)
	}
	slog.SetDefault(slog.New(h))
}
