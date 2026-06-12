package controller

import (
	"encoding/json"
	"strconv"
	"strings"
	"sync"

	corev1 "k8s.io/api/core/v1"
)

// ServiceConfig holds DNS settings extracted from a service's annotations.
type ServiceConfig struct {
	Namespace        string
	Name             string
	Hostnames        []string
	TTL              int
	Proxied          bool
	ControlPlaneOnly bool // if true, only control-plane nodes are announced
}

// ServiceStore is a thread-safe in-memory cache of ServiceConfigs, kept
// up-to-date by the service informer — no K8s API calls needed during reconciliation.
type ServiceStore struct {
	mu      sync.RWMutex
	configs map[string]*ServiceConfig // key: namespace/name
}

func NewServiceStore() *ServiceStore {
	return &ServiceStore{configs: make(map[string]*ServiceConfig)}
}

// Update upserts or removes the config for a service based on its annotations.
func (s *ServiceStore) Update(svc *corev1.Service) {
	cfg := parseServiceConfig(svc)
	key := svc.Namespace + "/" + svc.Name
	s.mu.Lock()
	defer s.mu.Unlock()
	if cfg == nil {
		delete(s.configs, key)
	} else {
		s.configs[key] = cfg
	}
}

// Delete removes a service config.
func (s *ServiceStore) Delete(key string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.configs, key)
}

// List returns a snapshot of all current service configs.
func (s *ServiceStore) List() []*ServiceConfig {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]*ServiceConfig, 0, len(s.configs))
	for _, v := range s.configs {
		out = append(out, v)
	}
	return out
}

func parseServiceConfig(svc *corev1.Service) *ServiceConfig {
	ann := svc.Annotations
	if ann == nil || ann["epictetus.io/dns-enabled"] != "true" {
		return nil
	}

	var hostnames []string

	if h := ann["epictetus.io/hostnames"]; h != "" {
		var parsed []string
		if err := json.Unmarshal([]byte(h), &parsed); err == nil {
			for _, p := range parsed {
				if t := strings.TrimSpace(p); t != "" {
					hostnames = append(hostnames, t)
				}
			}
		} else {
			for _, p := range strings.Split(h, ",") {
				if t := strings.TrimSpace(p); t != "" {
					hostnames = append(hostnames, t)
				}
			}
		}
	} else if h := ann["epictetus.io/hostname"]; h != "" {
		if t := strings.TrimSpace(h); t != "" {
			hostnames = []string{t}
		}
	}

	if len(hostnames) == 0 {
		return nil
	}

	ttl := 300
	if v := ann["epictetus.io/ttl"]; v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			ttl = n
		}
	}

	return &ServiceConfig{
		Namespace:        svc.Namespace,
		Name:             svc.Name,
		Hostnames:        hostnames,
		TTL:              ttl,
		Proxied:          ann["epictetus.io/proxied"] == "true",
		ControlPlaneOnly: ann["epictetus.io/control-plane-only"] == "true",
	}
}
