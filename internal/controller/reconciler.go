package controller

import (
	"context"
	"fmt"
	"log/slog"
	"sync"
	"sync/atomic"

	"github.com/magicorn/epictetus/internal/cloudflare"
	"github.com/magicorn/epictetus/internal/config"
	corev1 "k8s.io/api/core/v1"
)

// Reconciler holds the DNS reconciliation logic. It is called by the controller's
// workers and never makes K8s API calls — it reads from the informer cache and
// ServiceStore, then fans out Cloudflare operations concurrently.
type Reconciler struct {
	cf      *cloudflare.Client
	store   *ServiceStore
	cfg     *config.Config
	ipCache sync.Map   // node name -> external IP string
	ready   atomic.Bool
}

func NewReconciler(cf *cloudflare.Client, store *ServiceStore, cfg *config.Config) *Reconciler {
	return &Reconciler{cf: cf, store: store, cfg: cfg}
}

// Ready returns true once the reconciler has completed at least one successful sync.
func (r *Reconciler) Ready() bool { return r.ready.Load() }

// Reconcile brings Cloudflare DNS records for a node in line with desired state.
// Called for ADDED and MODIFIED events. Zero K8s API calls.
func (r *Reconciler) Reconcile(ctx context.Context, node *corev1.Node) error {
	ip := extractExternalIP(node)
	if ip != "" {
		r.ipCache.Store(node.Name, ip)
	} else {
		// Use cached IP if the node temporarily has no address
		if cached, ok := r.ipCache.Load(node.Name); ok {
			ip = cached.(string)
		}
	}

	if ip == "" {
		slog.Debug("node has no external IP, skipping", "node", node.Name)
		return nil
	}

	shouldRemove := r.shouldRemoveFromDNS(node)
	configs := r.store.List()

	if shouldRemove {
		slog.Info("node unhealthy — removing from dns",
			"node", node.Name, "ip", ip,
			"taints", taintKeys(node.Spec.Taints),
			"ready", readyCondition(node.Status.Conditions),
			"services", len(configs))
	} else {
		slog.Info("node healthy — ensuring dns records",
			"node", node.Name, "ip", ip, "services", len(configs))
	}

	return r.fanOut(ctx, ip, shouldRemove, isControlPlane(node), configs)
}

// ReconcileDeleted removes all DNS records for a deleted node using the cached IP.
func (r *Reconciler) ReconcileDeleted(ctx context.Context, nodeName string) error {
	cached, ok := r.ipCache.Load(nodeName)
	if !ok {
		slog.Warn("deleted node has no cached ip, nothing to clean up", "node", nodeName)
		return nil
	}
	ip := cached.(string)
	r.ipCache.Delete(nodeName)

	configs := r.store.List()
	slog.Info("node deleted — removing dns records", "node", nodeName, "ip", ip, "services", len(configs))
	// controlPlane=false is irrelevant here since remove=true always wins in fanOut.
	return r.fanOut(ctx, ip, true, false, configs)
}

// PerformFullSync reconciles all nodes against Cloudflare.
// For each hostname it makes exactly one read call, then concurrent writes.
func (r *Reconciler) PerformFullSync(ctx context.Context, nodes []*corev1.Node) error {
	allHealthyIPs := make(map[string]struct{})
	controlPlaneIPs := make(map[string]struct{})
	for _, node := range nodes {
		ip := extractExternalIP(node)
		if ip == "" || r.shouldRemoveFromDNS(node) {
			continue
		}
		allHealthyIPs[ip] = struct{}{}
		if isControlPlane(node) {
			controlPlaneIPs[ip] = struct{}{}
		}
		r.ipCache.Store(node.Name, ip)
	}

	configs := r.store.List()
	slog.Info("full sync started",
		"healthy_nodes", len(allHealthyIPs),
		"control_plane_nodes", len(controlPlaneIPs),
		"services", len(configs))

	// Deduplicate hostnames; last-seen settings win for duplicates.
	type hostEntry struct {
		ttl              int
		proxied          bool
		controlPlaneOnly bool
	}
	hostnames := make(map[string]hostEntry)
	for _, svc := range configs {
		for _, h := range svc.Hostnames {
			hostnames[h] = hostEntry{ttl: svc.TTL, proxied: svc.Proxied, controlPlaneOnly: svc.ControlPlaneOnly}
		}
	}

	var wg sync.WaitGroup
	errs := make(chan error, len(hostnames)*2)

	for hostname, entry := range hostnames {
		wg.Add(1)
		go func(hostname string, ttl int, proxied bool, controlPlaneOnly bool) {
			defer wg.Done()

			validIPs := allHealthyIPs
			if controlPlaneOnly {
				validIPs = controlPlaneIPs
			}

			records, err := r.cf.GetRecords(ctx, hostname)
			if err != nil {
				errs <- fmt.Errorf("get records %s: %w", hostname, err)
				return
			}

			currentByIP := make(map[string]cloudflare.Record, len(records))
			for _, rec := range records {
				currentByIP[rec.Content] = rec
			}

			// Remove stale records (includes worker IPs if control-plane-only)
			for ip, rec := range currentByIP {
				if _, ok := validIPs[ip]; !ok {
					if err := r.cf.DeleteRecord(ctx, rec.ID, rec.ZoneID); err != nil {
						errs <- fmt.Errorf("delete stale %s->%s: %w", hostname, ip, err)
					} else {
						slog.Info("sync: deleted stale record", "hostname", hostname, "ip", ip)
					}
				}
			}

			// Add missing records
			for ip := range validIPs {
				if _, exists := currentByIP[ip]; !exists {
					if err := r.cf.CreateRecord(ctx, hostname, ip, ttl, proxied); err != nil {
						errs <- fmt.Errorf("create missing %s->%s: %w", hostname, ip, err)
					}
				}
			}
		}(hostname, entry.ttl, entry.proxied, entry.controlPlaneOnly)
	}

	wg.Wait()
	close(errs)

	var errCount int
	for err := range errs {
		errCount++
		slog.Error("sync error", "err", err)
	}
	if errCount > 0 {
		return fmt.Errorf("%d errors during full sync", errCount)
	}

	r.ready.Store(true)
	return nil
}

// fanOut concurrently applies a DNS add or remove operation across all hostnames
// in all managed services for a given node IP.
// nodeIsControlPlane is used to enforce the epictetus.io/control-plane-only annotation:
// if a service requires control-plane-only and this node is a worker, it is treated as remove.
func (r *Reconciler) fanOut(ctx context.Context, ip string, remove bool, nodeIsControlPlane bool, configs []*ServiceConfig) error {
	var wg sync.WaitGroup
	errs := make(chan error, 32)

	for _, svc := range configs {
		for _, hostname := range svc.Hostnames {
			wg.Add(1)
			effectiveRemove := remove || (svc.ControlPlaneOnly && !nodeIsControlPlane)
			go func(hostname string, ttl int, proxied bool, doRemove bool) {
				defer wg.Done()
				var err error
				if doRemove {
					err = r.removeIPFromHostname(ctx, hostname, ip)
				} else {
					err = r.ensureRecord(ctx, hostname, ip, ttl, proxied)
				}
				if err != nil {
					errs <- fmt.Errorf("%s: %w", hostname, err)
				}
			}(hostname, svc.TTL, svc.Proxied, effectiveRemove)
		}
	}

	wg.Wait()
	close(errs)

	var errCount int
	for err := range errs {
		errCount++
		slog.Error("dns operation failed", "ip", ip, "err", err)
	}
	if errCount > 0 {
		return fmt.Errorf("%d dns operations failed for ip %s", errCount, ip)
	}
	return nil
}

func (r *Reconciler) ensureRecord(ctx context.Context, hostname, ip string, ttl int, proxied bool) error {
	records, err := r.cf.GetRecords(ctx, hostname)
	if err != nil {
		return err
	}
	for _, rec := range records {
		if rec.Content == ip {
			slog.Debug("dns record already exists", "hostname", hostname, "ip", ip)
			return nil
		}
	}
	return r.cf.CreateRecord(ctx, hostname, ip, ttl, proxied)
}

func (r *Reconciler) removeIPFromHostname(ctx context.Context, hostname, ip string) error {
	records, err := r.cf.GetRecords(ctx, hostname)
	if err != nil {
		return err
	}
	for _, rec := range records {
		if rec.Content == ip {
			if err := r.cf.DeleteRecord(ctx, rec.ID, rec.ZoneID); err != nil {
				return err
			}
			slog.Info("deleted dns record", "hostname", hostname, "ip", ip)
		}
	}
	return nil
}

func (r *Reconciler) shouldRemoveFromDNS(node *corev1.Node) bool {
	presentDeletionTaints := 0
	for _, t := range node.Spec.Taints {
		if _, ok := r.cfg.DeletionTaints[t.Key]; ok {
			presentDeletionTaints++
		}
		if _, ok := r.cfg.UnhealthyTaints[t.Key]; ok {
			return true
		}
	}
	if len(r.cfg.DeletionTaints) > 0 && presentDeletionTaints == len(r.cfg.DeletionTaints) {
		return true
	}
	for _, c := range node.Status.Conditions {
		if c.Type == corev1.NodeReady {
			if c.Status == corev1.ConditionFalse && r.cfg.RemoveOnNotReady {
				return true
			}
			if c.Status == corev1.ConditionUnknown && r.cfg.RemoveOnUnreachable {
				return true
			}
		}
	}
	return false
}

// extractExternalIP implements the three-stage fallback: cloud ExternalIP →
// Flannel annotation → magicorn custom label.
func extractExternalIP(node *corev1.Node) string {
	for _, addr := range node.Status.Addresses {
		if addr.Type == corev1.NodeExternalIP {
			return addr.Address
		}
	}
	if ip := node.Annotations["flannel.alpha.coreos.com/public-ip"]; ip != "" {
		return ip
	}
	if ip := node.Labels["k8s.magicorn.net/external-ip"]; ip != "" {
		return ip
	}
	return ""
}

// isControlPlane returns true if the node carries the standard control-plane role label.
// Both the current label (control-plane) and the legacy label (master) are checked.
func isControlPlane(node *corev1.Node) bool {
	_, cp := node.Labels["node-role.kubernetes.io/control-plane"]
	_, master := node.Labels["node-role.kubernetes.io/master"]
	return cp || master
}

func taintKeys(taints []corev1.Taint) []string {
	keys := make([]string, len(taints))
	for i, t := range taints {
		keys[i] = t.Key
	}
	return keys
}

func readyCondition(conditions []corev1.NodeCondition) string {
	for _, c := range conditions {
		if c.Type == corev1.NodeReady {
			return string(c.Status)
		}
	}
	return "Unknown"
}
