package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

type Config struct {
	CloudflareAPIToken  string
	SyncInterval        int
	KubeconfigPath      string
	DeletionTaints      map[string]struct{}
	UnhealthyTaints     map[string]struct{}
	RemoveOnNotReady    bool
	RemoveOnUnreachable bool
	LogLevel            string
	LogFormat           string
	HealthPort          int
	MaxRetries          int
	Workers          int
	CFMaxConcurrency int
}

func Load() (*Config, error) {
	token := os.Getenv("CLOUDFLARE_API_TOKEN")
	if token == "" {
		return nil, fmt.Errorf("CLOUDFLARE_API_TOKEN is required")
	}

	syncInterval := envInt("DNS_SYNC_INTERVAL", 60)
	if syncInterval < 10 {
		return nil, fmt.Errorf("DNS_SYNC_INTERVAL must be at least 10 seconds")
	}

	return &Config{
		CloudflareAPIToken: token,
		SyncInterval:       syncInterval,
		KubeconfigPath:     os.Getenv("K8S_CONFIG_PATH"),
		DeletionTaints: setOf(
			"DeletionCandidateOfClusterAutoscaler",
			"ToBeDeletedByClusterAutoscaler",
		),
		UnhealthyTaints:     parseUnhealthyTaints(envString("UNHEALTHY_NODE_TAINTS", "critical")),
		RemoveOnNotReady:    envBool("REMOVE_ON_NOT_READY", true),
		RemoveOnUnreachable: envBool("REMOVE_ON_UNREACHABLE", true),
		LogLevel:            envString("LOG_LEVEL", "INFO"),
		LogFormat:           envString("LOG_FORMAT", "console"),
		HealthPort:          envInt("HEALTH_PORT", 8080),
		MaxRetries:          envInt("MAX_RETRIES", 3),
		Workers:            envInt("WORKER_COUNT", 4),
	CFMaxConcurrency:   envInt("CF_MAX_CONCURRENCY", 10),
	}, nil
}

func parseUnhealthyTaints(s string) map[string]struct{} {
	critical := []string{
		"node.kubernetes.io/not-ready",
		"node.kubernetes.io/unreachable",
		"node.kubernetes.io/network-unavailable",
		"node.kubernetes.io/out-of-service",
		"node.cilium.io/agent-not-ready",
	}
	switch strings.ToLower(s) {
	case "", "critical":
		return setOf(critical...)
	case "none":
		return map[string]struct{}{}
	case "all":
		return setOf(append(critical, "node.kubernetes.io/unschedulable")...)
	default:
		if strings.HasPrefix(s, "custom:") {
			parts := strings.Split(s[7:], ",")
			keys := make([]string, 0, len(parts))
			for _, p := range parts {
				if t := strings.TrimSpace(p); t != "" {
					keys = append(keys, t)
				}
			}
			return setOf(keys...)
		}
		return setOf(critical...)
	}
}

func setOf(keys ...string) map[string]struct{} {
	m := make(map[string]struct{}, len(keys))
	for _, k := range keys {
		m[k] = struct{}{}
	}
	return m
}

func envString(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func envInt(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

func envBool(key string, def bool) bool {
	if v := os.Getenv(key); v != "" {
		return strings.ToLower(v) == "true"
	}
	return def
}
