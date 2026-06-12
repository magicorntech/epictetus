package cloudflare

import (
	"context"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"time"

	cf "github.com/cloudflare/cloudflare-go"
)

type Record struct {
	ID      string
	Name    string
	Content string
	TTL     int
	Proxied bool
	ZoneID  string
}

type Client struct {
	api           *cf.API
	mu            sync.RWMutex
	zones         map[string]string // zone name -> zone ID
	hostnameZones sync.Map          // hostname -> zone ID
	sema          chan struct{}      // concurrency limiter for CF API calls
}

func NewClient(token string, maxConcurrency int) (*Client, error) {
	api, err := cf.NewWithAPIToken(token)
	if err != nil {
		return nil, fmt.Errorf("cloudflare auth: %w", err)
	}
	c := &Client{
		api:   api,
		zones: make(map[string]string),
		sema:  make(chan struct{}, maxConcurrency),
	}
	if err := c.refreshZones(context.Background()); err != nil {
		return nil, fmt.Errorf("loading zones: %w", err)
	}
	return c, nil
}

// acquire blocks until a concurrency slot is available or ctx is cancelled.
func (c *Client) acquire(ctx context.Context) error {
	select {
	case c.sema <- struct{}{}:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (c *Client) release() { <-c.sema }

// withRetry runs fn up to 3 times with exponential backoff (1s, 2s, 4s).
// Each attempt acquires and releases a concurrency slot.
func (c *Client) withRetry(ctx context.Context, fn func() error) error {
	delays := []time.Duration{time.Second, 2 * time.Second, 4 * time.Second}
	var err error
	for attempt, delay := range delays {
		if err = c.acquire(ctx); err != nil {
			return err
		}
		err = fn()
		c.release()
		if err == nil {
			return nil
		}
		if attempt < len(delays)-1 {
			slog.Debug("cf api error, retrying", "attempt", attempt+1, "wait", delay, "err", err)
			select {
			case <-time.After(delay):
			case <-ctx.Done():
				return ctx.Err()
			}
		}
	}
	return err
}

func (c *Client) refreshZones(ctx context.Context) error {
	zones, err := c.api.ListZones(ctx)
	if err != nil {
		return fmt.Errorf("list zones: %w", err)
	}
	c.mu.Lock()
	c.zones = make(map[string]string, len(zones))
	for _, z := range zones {
		c.zones[z.Name] = z.ID
	}
	c.mu.Unlock()

	// Invalidate hostname cache — zone IDs may have changed after a zone
	// was removed and re-added in Cloudflare.
	c.hostnameZones.Range(func(k, _ any) bool {
		c.hostnameZones.Delete(k)
		return true
	})

	slog.Debug("refreshed cloudflare zones", "count", len(zones))
	return nil
}

func (c *Client) zoneForHostname(hostname string) (string, error) {
	if id, ok := c.hostnameZones.Load(hostname); ok {
		return id.(string), nil
	}

	parts := strings.Split(hostname, ".")
	c.mu.RLock()
	defer c.mu.RUnlock()
	for i := range parts {
		domain := strings.Join(parts[i:], ".")
		if id, ok := c.zones[domain]; ok {
			c.hostnameZones.Store(hostname, id)
			return id, nil
		}
	}
	return "", fmt.Errorf("no cloudflare zone for hostname %q", hostname)
}

// GetRecords returns all A records for a hostname.
func (c *Client) GetRecords(ctx context.Context, hostname string) ([]Record, error) {
	zoneID, err := c.zoneForHostname(hostname)
	if err != nil {
		return nil, err
	}

	var recs []cf.DNSRecord
	err = c.withRetry(ctx, func() error {
		var apiErr error
		recs, _, apiErr = c.api.ListDNSRecords(ctx, cf.ZoneIdentifier(zoneID), cf.ListDNSRecordsParams{
			Name: hostname,
			Type: "A",
		})
		return apiErr
	})
	if err != nil {
		return nil, fmt.Errorf("list dns records %s: %w", hostname, err)
	}

	out := make([]Record, 0, len(recs))
	for _, r := range recs {
		proxied := false
		if r.Proxied != nil {
			proxied = *r.Proxied
		}
		out = append(out, Record{
			ID:      r.ID,
			Name:    r.Name,
			Content: r.Content,
			TTL:     r.TTL,
			Proxied: proxied,
			ZoneID:  zoneID,
		})
	}
	return out, nil
}

// CreateRecord creates a new DNS A record. Does not check for duplicates — callers must do that.
func (c *Client) CreateRecord(ctx context.Context, hostname, ip string, ttl int, proxied bool) error {
	zoneID, err := c.zoneForHostname(hostname)
	if err != nil {
		return err
	}

	return c.withRetry(ctx, func() error {
		_, err := c.api.CreateDNSRecord(ctx, cf.ZoneIdentifier(zoneID), cf.CreateDNSRecordParams{
			Type:    "A",
			Name:    hostname,
			Content: ip,
			TTL:     ttl,
			Proxied: &proxied,
		})
		if err != nil {
			return fmt.Errorf("create dns record %s -> %s: %w", hostname, ip, err)
		}
		slog.Info("created dns record", "hostname", hostname, "ip", ip, "ttl", ttl, "proxied", proxied)
		return nil
	})
}

// DeleteRecord deletes a DNS record by ID.
func (c *Client) DeleteRecord(ctx context.Context, recordID, zoneID string) error {
	return c.withRetry(ctx, func() error {
		if err := c.api.DeleteDNSRecord(ctx, cf.ZoneIdentifier(zoneID), recordID); err != nil {
			return fmt.Errorf("delete dns record %s: %w", recordID, err)
		}
		return nil
	})
}

// HealthCheck validates Cloudflare connectivity by refreshing the zone list.
func (c *Client) HealthCheck(ctx context.Context) error {
	return c.refreshZones(ctx)
}

// AvailableZones returns the list of known zone names.
func (c *Client) AvailableZones() []string {
	c.mu.RLock()
	defer c.mu.RUnlock()
	zones := make([]string, 0, len(c.zones))
	for name := range c.zones {
		zones = append(zones, name)
	}
	return zones
}
