# Epictetus Tests

The Python test suite (`test_*.py`) covered the old Python implementation and is kept for reference. The Go rewrite uses `go test`.

## Running Go Tests

```bash
# All packages
go test ./...

# Verbose
go test -v ./...

# Specific package
go test ./internal/controller/...
go test ./internal/cloudflare/...
```

## Test Coverage

```bash
go test -coverprofile=coverage.out ./...
go tool cover -html=coverage.out
```

## What to Test

### ServiceStore (`internal/controller/store.go`)
- `parseServiceConfig` with `epictetus.io/hostname` (legacy)
- `parseServiceConfig` with `epictetus.io/hostnames` comma-separated
- `parseServiceConfig` with `epictetus.io/hostnames` JSON array
- Malformed JSON falls back to comma split
- Missing `dns-enabled=true` returns nil
- TTL and proxied annotation parsing

### Reconciler (`internal/controller/reconciler.go`)
- `shouldRemoveFromDNS` with deletion taints (requires both)
- `shouldRemoveFromDNS` with single deletion taint (should NOT remove)
- `shouldRemoveFromDNS` with unhealthy taint (any → remove)
- `shouldRemoveFromDNS` with Ready=False / Ready=Unknown
- `extractExternalIP` three-stage fallback

### Controller (`internal/controller/controller.go`)
- `nodeStateChanged` ignores heartbeat MODIFIED events
- `nodeStateChanged` detects taint changes
- `nodeStateChanged` detects ready condition changes
- `nodeStateChanged` detects IP changes

## Legacy Python Tests

The original Python tests are preserved for reference:

```bash
python3 tests/test_multiple_hostnames.py
python3 tests/test_node_health.py
python3 tests/test_updated_config.py
python3 tests/run_all_tests.py
```

These test the old Python implementation and will not pass against the Go rewrite.
