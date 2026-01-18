# ansible-role-freesocks

An Ansible role for deploying and managing Outline VPN servers for [FreeSocks](https://freesocks.org/). This role supports both new server deployments and migrations between servers, with features including:

- Automated Outline server installation and configuration
- Pluggable provider architecture for DNS, tunnel, and KV store
- Cloudflare integration (DNS, Tunnel, KV) with more providers coming soon
- Hostname rotation for DNS blocking bypass
- Server migration capabilities

## Requirements

- Debian-based Linux system (tested on Debian only)
- Python 3.x
- Docker (will be installed by the role)
- Provider-specific requirements (see Provider Configuration)

## Provider Configuration

The role uses a pluggable provider architecture. All providers must be explicitly set via `--extra-vars`.

| Provider | Options | Purpose |
|----------|---------|---------|
| `dns_provider` | `cloudflare` | DNS record management |
| `tunnel_provider` | `cloudflare`, `none` | Secure tunnel for API/Prometheus access |
| `kv_provider` | `cloudflare` | Server endpoint storage |

### Cloudflare Provider Requirements

When using Cloudflare providers, you need:
- API token with appropriate permissions
- Account ID and Zone ID(s)
- KV namespaces (for `kv_provider: cloudflare`)
- Access Team configuration (for Prometheus access via tunnel)

## Role Variables

### Required Variables

```yaml
# Provider Configuration (required via --extra-vars)
dns_provider: "cloudflare"
tunnel_provider: "cloudflare"  # or "none" for direct port access
kv_provider: "cloudflare"

# Environment Mode (required via --extra-vars)  
environment_mode: "prod"  # or "dev"

# Operation Mode
operation_mode: "deploy"  # or "migrate"

# Cloudflare Configuration (when using Cloudflare providers)
cloudflare_api_endpoint: "https://api.cloudflare.com/client/v4"
cloudflare_api_token: "your-api-token"  # API Token (not Global API Key)
cloudflare_account_id: "your-account-id"
cloudflare_zone_id: "your-zone-id"
cloudflare_access_team_name: "your-team-name"
cloudflare_access_aud_tag: "your-aud-tag"

# KV Namespace Configuration
cloudflare_api_kv_namespace_prod: "your-prod-api-kv-namespace"
cloudflare_prom_kv_namespace_prod: "your-prod-prom-kv-namespace"
cloudflare_api_kv_namespace_dev: "your-dev-api-kv-namespace"
cloudflare_prom_kv_namespace_dev: "your-dev-prom-kv-namespace"

# Domain Configuration
# Zone IDs are looked up from domain_providers based on the domain
# Optionally include per-domain credentials for multi-account setups
domain_providers:
  example.com:
    dns_provider: cloudflare
    tunnel_provider: none
    zone_id: "your-zone-id-for-com"
  example.app:
    dns_provider: cloudflare
    tunnel_provider: none
    zone_id: "your-zone-id-for-app"
  # Domain on a different Cloudflare account
  other-domain.com:
    dns_provider: cloudflare
    tunnel_provider: none
    zone_id: "zone-id-for-other-account"
    # Override credentials for this domain
    cloudflare_api_token: "your-api-token-for-other-account"
    cloudflare_account_id: "account-id-for-other-account"  # Needed for tunnels

# Active domain for operations (typically set via deploy_target_domain or change_target_domain)
base_domain: "example.com"
api_domain: "example.com"
prom_domain: "example.com"
kv_hostname_prefix: "outline"

# Envoy Mappings (for multi-IP DNS records)
envoy_mappings:
  outline1-ams:
    ipv4: ["1.2.3.4", "5.6.7.8"]
    ipv6: ["2001:db8::1", "2001:db8::2"]
```

### Optional Variables

```yaml
# Server Configuration
outline_keys_port: 443
outline_api_port: 8443
cloudflared_os_version: "bookworm"
hostname_extension: ""

# Custom hostname override (optional)
# If set, uses this instead of auto-generating random hostname
custom_hostname: "my-server"  # Example: results in my-server.example.com

# Number of words in randomly generated hostnames
hostname_word_count: 3  # Default: 3 (e.g., apple-banana-cherry)

# DNS Proxy (Cloudflare orange cloud, Fastly shield, etc.)
# When true, traffic is proxied through the CDN
dns_proxied: false  # Set to true for CDN proxy mode

# Migration Settings
migrate_delete_source: false
source_hostname: ""
destination_hostname: ""
```

### WebSocket (WSS) Support

Enable Shadowsocks over WebSocket for improved censorship resistance. This tunnels Shadowsocks traffic over HTTPS, making it appear as regular web traffic.

```yaml
# Enable WSS support
outline_wss_enabled: true

# Caddy configuration for automatic HTTPS
outline_caddy_auto_https: true
outline_caddy_email: "admin@example.com"
outline_caddy_domain: ""  # Defaults to server hostname

# WebSocket path configuration
# Random paths use dictionary words (like hostnames) for natural-looking URLs
outline_wss_random_paths: true  # Set to false to use custom paths
outline_wss_random_path_min_words: 3  # Minimum words (e.g., /apple-banana-cherry)
outline_wss_random_path_max_words: 5  # Maximum words (random in range)

# Custom paths (used when outline_wss_random_paths is false)
outline_wss_tcp_path: "/tcp"
outline_wss_udp_path: "/udp"

# Internal WebSocket server port (not externally exposed)
outline_wss_server_port: 8080

# API Proxy - enables valid TLS for API access (for control planes, etc.)
outline_api_proxy_path: "/api"  # API available at https://domain/api/...

# Hostname suffixes for API and Prometheus endpoints
# Empty when using Caddy proxy (everything goes through port 443)
# Set to "-api"/"-prom" for legacy separate subdomains
api_hostname_suffix: ""   # e.g., "" -> abc123.domain.com, "-api" -> abc123-api.domain.com
prom_hostname_suffix: ""
```

> **Important:** When using WSS, set `outline_keys_port` to a non-443 port (e.g., 853) 
> so that Caddy can use port 443 for HTTPS/WebSocket traffic.

### slipstream DNS Tunnel Support

Enable DNS tunneling for extreme censorship resistance. Traffic is tunneled through DNS queries via recursive resolvers (e.g., Yandex DNS on Russia's allowlist).

**Note:** slipstream builds from source, requiring Rust on the target server. The build can take several minutes.

#### Configuration

```yaml
# Enable slipstream DNS tunnel
slipstream_enabled: true

# Mode: "shadowsocks" (default) or "raw"
# - shadowsocks: Tunnel to local Shadowsocks (client needs ss-local)
# - raw: Direct SOCKS5 proxy via microsocks (no ss-local needed)
slipstream_mode: "shadowsocks"

# Client resolvers (Yandex DNS on Russia allowlist)
slipstream_resolver: "77.88.8.8:53"
slipstream_resolver_backup: "77.88.8.1:53"

# Version and repository
slipstream_version: "main"
slipstream_repo_url: "https://github.com/Mygod/slipstream-rust.git"

# DNS listen port (default: 53)
slipstream_dns_port: 53

# Raw mode: SOCKS5 proxy port
slipstream_socks_port: 1080
```

**Required DNS Variables** (must be set via `--extra-vars`):
```yaml
slipstream_base_domain: "your-dns.com"  # REQUIRED - Must be in domain_providers
slipstream_subdomain: "dns1"             # REQUIRED - Tunnel subdomain
slipstream_ns_hostname: "ns1"            # REQUIRED - Nameserver hostname
slipstream_create_dns_records: true      # Optional - Auto-create DNS records (default: true)
```

#### DNS Setup (Automatic)

When `slipstream_create_dns_records: true` (default), the role automatically creates:

```dns
dns1.your-dns.com.  IN NS    ns1.your-dns.com.
ns1.your-dns.com.   IN A     <server-ipv4>
ns1.your-dns.com.   IN AAAA  <server-ipv6>
```

**Natural-Looking Subdomain Examples:**
- `dns1`, `dns2` - looks like DNS infrastructure
- `mail1`, `mail2` - looks like mail servers  
- `ns1`, `ns2` - looks like nameservers
- `api1`, `cdn1` - looks like infrastructure

**Multiple Servers:** Use different subdomains for each server:
```bash
# Server 1: dns1.your-dns.com
--extra-vars "slipstream_subdomain=dns1 slipstream_ns_hostname=ns1"

# Server 2: dns2.your-dns.com  
--extra-vars "slipstream_subdomain=dns2 slipstream_ns_hostname=ns2"
```

**Requirements:**
- `slipstream_base_domain` must exist in `domain_providers` with a valid `zone_id`
- Cloudflare API credentials must be configured

**Manual Setup** (if `slipstream_create_dns_records: false`):
Create the DNS records manually in your DNS provider's dashboard.

#### Mode Comparison

| Feature | `shadowsocks` mode | `raw` mode |
|---------|-------------------|------------|
| Server target | outline-ss-server:443 | microsocks (SOCKS5):1080 |
| Client needs | slipstream-client + ss-local | slipstream-client only |
| Encryption layers | QUIC + Shadowsocks | QUIC only |
| Setup complexity | Higher | Simpler |
| Best for | Outline integration | Standalone proxy |

#### Client Usage: Shadowsocks Mode

```bash
# Build slipstream-client
git clone https://github.com/Mygod/slipstream-rust.git
cd slipstream-rust && git submodule update --init --recursive
cargo build --release -p slipstream-client

# Start DNS tunnel (use your actual domain from deployment)
./target/release/slipstream-client \
  --tcp-listen-port 7000 \
  --resolver 77.88.8.8:53 \
  --domain dns1.your-dns.com \
  --cert /path/to/server-cert.pem

# Connect ss-local through tunnel
ss-local -s 127.0.0.1 -p 7000 -l 1080 -k <password> -m chacha20-ietf-poly1305

# Use SOCKS proxy at 127.0.0.1:1080
```

#### Client Usage: Raw Mode

```bash
# Build slipstream-client (same as above)
# ...

# Start DNS tunnel - this IS your SOCKS proxy!
./target/release/slipstream-client \
  --tcp-listen-port 1080 \
  --resolver 77.88.8.8:53 \
  --domain dns1.your-dns.com \
  --cert /path/to/server-cert.pem

# Configure apps to use SOCKS5 at 127.0.0.1:1080
# No ss-local needed - slipstream-client IS the proxy!
```


## Quick Reference Guide

### Deploy Mode Examples

```bash
# Basic Outline server
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=deploy environment_mode=prod" \
  --extra-vars "deploy_target_domain=example.com"

# Outline + WebSocket (CDN fronting)
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=deploy environment_mode=prod" \
  --extra-vars "deploy_target_domain=example.com" \
  --extra-vars "outline_wss_enabled=true"

# Outline + slipstream (DNS tunnel to SS server)
# DNS records are auto-created from slipstream_base_domain
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=deploy environment_mode=prod" \
  --extra-vars "deploy_target_domain=example.com" \
  --extra-vars "slipstream_enabled=true slipstream_base_domain=your-dns.com"

# Outline + slipstream raw (two independent transports)
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=deploy environment_mode=prod" \
  --extra-vars "deploy_target_domain=example.com" \
  --extra-vars "slipstream_enabled=true slipstream_mode=raw slipstream_base_domain=your-dns.com"

# slipstream only (raw mode, no Outline)
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=deploy environment_mode=prod" \
  --extra-vars "deploy_target_domain=example.com" \
  --extra-vars "outline_enabled=false slipstream_enabled=true slipstream_mode=raw slipstream_base_domain=your-dns.com"

# Full stack (all transports)
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=deploy environment_mode=prod" \
  --extra-vars "deploy_target_domain=example.com" \
  --extra-vars "outline_wss_enabled=true slipstream_enabled=true slipstream_base_domain=your-dns.com"
```

### Change Mode Examples

```bash
# Rotate hostname (same domain)
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=change environment_mode=prod" \
  --extra-vars "change_target_domain=example.com"

# Change to different domain
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=change environment_mode=prod" \
  --extra-vars "change_target_domain=example.app"

# Keep old records
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=change environment_mode=prod" \
  --extra-vars "change_target_domain=example.app" \
  --extra-vars "change_delete_old_dns=false change_delete_old_kv=false"
```

### Migrate Mode Examples

```bash
# Migrate server to new host
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=migrate environment_mode=prod" \
  --extra-vars "source_hostname=old-server source_kv_hostname=apple-banana" \
  --extra-vars "destination_hostname=new-server destination_kv_hostname=apple-banana" \
  --extra-vars "dns_provider=cloudflare tunnel_provider=cloudflare kv_provider=cloudflare"

# Migrate and delete source entries
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=migrate environment_mode=prod" \
  --extra-vars "source_hostname=old-server source_kv_hostname=apple-banana" \
  --extra-vars "destination_hostname=new-server destination_kv_hostname=apple-banana" \
  --extra-vars "migrate_delete_source=true"
```

### Update Mode Examples

```bash
# Add slipstream (raw mode) to existing Outline server
# DNS records auto-created: dns1.your-dns.com → ns1.your-dns.com → server IP
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=update environment_mode=prod" \
  --extra-vars "slipstream_enabled=true slipstream_mode=raw slipstream_base_domain=your-dns.com"

# Add slipstream (shadowsocks mode) to existing Outline server
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=update environment_mode=prod" \
  --extra-vars "slipstream_enabled=true slipstream_base_domain=your-dns.com"

# Add second slipstream server (dns2.your-dns.com)
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=update environment_mode=prod" \
  --extra-vars "slipstream_enabled=true slipstream_mode=raw slipstream_base_domain=your-dns.com" \
  --extra-vars "slipstream_subdomain=dns2 slipstream_ns_hostname=ns2"

# Add WebSocket to existing server
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=update environment_mode=prod" \
  --extra-vars "outline_wss_enabled=true"

# Add both slipstream and WebSocket
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=update environment_mode=prod" \
  --extra-vars "slipstream_enabled=true slipstream_mode=raw slipstream_base_domain=your-dns.com" \
  --extra-vars "outline_wss_enabled=true"
```

### Component Flags Reference

| Flag | Default | Description |
|------|---------|-------------|
| `outline_enabled` | `true` | Deploy Outline Shadowsocks server |
| `outline_wss_enabled` | `false` | Enable WebSocket transport (requires Outline) |
| `slipstream_enabled` | `false` | Deploy slipstream DNS tunnel |
| `slipstream_mode` | `shadowsocks` | `shadowsocks` (tunnel to SS) or `raw` (direct SOCKS5) |
| `slipstream_base_domain` | **required** | Base domain for DNS (must be in domain_providers) |
| `slipstream_subdomain` | **required** | Tunnel subdomain (dns1, mail1, etc.) |
| `slipstream_ns_hostname` | **required** | Nameserver hostname (ns1, ns2, etc.) |
| `slipstream_resolver` | `77.88.8.8:53` | DNS resolver for clients |
| `force_reinstall_slipstream` | `false` | Force reinstall slipstream even if already installed |
| `force_reinstall_wss` | `false` | Force reinstall WebSocket even if already installed |



## Operation Modes

### Deploy Mode

Deploys a server with selected components. Deploy mode is **component-based**, allowing various combinations:

**Component Selection:**
```yaml
# Components (set in playbook or via --extra-vars)
outline_enabled: true         # Outline Shadowsocks server (default: true)
slipstream_enabled: false     # slipstream DNS tunnel (default: false)
slipstream_mode: "shadowsocks"  # or "raw" for standalone SOCKS5
outline_wss_enabled: false    # WebSocket transport (requires Outline)
```

**Deployment Combinations:**
| Combination | Components | Use Case |
|-------------|------------|----------|
| Default | Outline only | Standard FreeSocks server |
| Outline + WSS | Outline + WebSocket | CDN-fronted censorship resistance |
| Outline + slipstream | Outline + slipstream (SS mode) | DNS tunnel to SS server |
| slipstream only | slipstream (raw mode) | Standalone DNS tunnel proxy |
| Full stack | Outline + WSS + slipstream | Maximum transport options |

**Steps:**
1. Validates component selection and configuration
2. Generates random hostname for the target domain
3. Installs base packages
4. Deploys enabled components (Outline, WebSocket, slipstream)
5. Sets up DNS records via configured provider
6. Configures tunnel (if `tunnel_provider != none`)
7. Updates KV store with server information

**Example Commands:**
```bash
# Default: Outline only
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=deploy deploy_target_domain=example.com"

# Outline + slipstream (shadowsocks mode)
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=deploy deploy_target_domain=example.com" \
  --extra-vars "slipstream_enabled=true slipstream_base_domain=your-dns.com"

# slipstream only (raw mode - no Outline needed)
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=deploy deploy_target_domain=example.com" \
  --extra-vars "outline_enabled=false slipstream_enabled=true slipstream_mode=raw"
```

### Migrate Mode

Migrates an existing Outline server to a new location:
1. Verifies source server exists in KV store
2. Verifies /opt/outline doesn't exist on destination
3. Installs new Outline server
4. Copies configuration from source to destination
5. Updates DNS and KV store entries
6. Optionally deletes source server KV entries

### Change Mode

Rotates the hostname on an existing server when the current hostname is blocked by DNS filtering. This mode supports changing to a different domain entirely (e.g., from `example.com` to `example.app`).

1. Validates `change_target_domain` is configured in `domain_providers`
2. Reads existing API info from server
3. Generates new random hostname for the target domain
4. Creates new DNS records using the domain's configured provider
5. Updates Caddy domain configuration (triggers new TLS certificate)
6. Updates server hostname setting
7. Updates local config files (shadowbox_server_config.json, access.txt, Caddy config)
8. **Restarts container** to apply changes
9. **Waits for health check** to confirm API accessibility
10. Updates KV store with new endpoint
11. Optionally deletes old DNS and KV entries

**Required Configuration:**
```yaml
# In your playbook vars
domain_providers:
  example.com:
    dns_provider: cloudflare
    tunnel_provider: cloudflare
    zone_id: "your-zone-id-for-com"
  example.app:
    dns_provider: cloudflare
    tunnel_provider: cloudflare
    zone_id: "your-zone-id-for-app"
  # Future providers (framework ready)
  # example.org:
  #   dns_provider: fastly
  #   tunnel_provider: none
```

**Required Variable:**
```yaml
# Must be passed via --extra-vars
change_target_domain: "example.app"  # Domain to generate new hostname for
```

**Optional Settings:**
```yaml
# Whether to delete old DNS records after hostname change
change_delete_old_dns: true
# Whether to delete old KV entries after hostname change
change_delete_old_kv: true
```

**Usage:**
```bash
# Change hostname to a new random hostname on example.app
ansible-playbook -i inventory playbook.yml \
  --extra-vars "operation_mode=change environment_mode=prod" \
  --extra-vars "change_target_domain=example.app"
```

This generates something like `apple-banana-cherry.example.app` and uses the Cloudflare provider configured for that domain.

### Update Mode

Adds new components (slipstream, WebSocket) to an **existing** server without reinstalling Outline. Automatically detects the existing hostname and installed components, and updates the KV store.

**Use Cases:**
- Add slipstream DNS tunnel to existing Outline server
- Add WebSocket transport to existing Outline server
- Switch slipstream modes (shadowsocks ↔ raw)
- Update slipstream configuration (resolvers, domain)
- **Recover from partial installations** (using force reinstall)

**Idempotent Behavior:**
- Components already installed are automatically skipped
- Use `force_reinstall_slipstream=true` or `force_reinstall_wss=true` to reinstall

**Steps:**
1. Detects existing Outline installation and hostname
2. Detects existing components (slipstream binary, WebSocket in config)
3. Installs requested components that aren't already installed (or force reinstall)
4. Updates KV store with new component configuration

**Usage:**
```bash
# Add slipstream raw to existing server
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=update environment_mode=prod" \
  --extra-vars "slipstream_enabled=true slipstream_mode=raw slipstream_base_domain=your-dns.com" \
  --extra-vars "slipstream_subdomain=dns1 slipstream_ns_hostname=ns1"

# Force reinstall slipstream (e.g., after partial failure or config change)
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=update environment_mode=prod" \
  --extra-vars "slipstream_enabled=true slipstream_mode=raw slipstream_base_domain=your-dns.com" \
  --extra-vars "slipstream_subdomain=dns1 slipstream_ns_hostname=ns1" \
  --extra-vars "force_reinstall_slipstream=true"

# Force reinstall WebSocket (regenerate config)
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=update environment_mode=prod" \
  --extra-vars "outline_wss_enabled=true force_reinstall_wss=true"
```


## Directory Structure

```
tasks/
├── main.yml                 # Orchestrator with provider routing
├── setup/
│   ├── install.yml          # Base package installation
│   ├── outline.yml          # Outline server setup
│   ├── websocket.yml        # WebSocket (WSS) configuration
│   └── slipstream.yml       # slipstream DNS tunnel setup
├── change/
│   └── change.yml           # Hostname change operations
├── migrate/
│   ├── migrate.yml          # Migration orchestration
│   ├── transfer_config.yml  # Config transfer (provider-agnostic)
│   └── containers.yml       # Container management
└── providers/
    └── cloudflare/
        ├── install.yml      # Cloudflared installation
        ├── dns.yml          # DNS management
        ├── tunnel.yml       # Tunnel setup
        ├── kv.yml           # KV store operations (JSON with slipstream)
        └── migrate/         # Migration-specific tasks

templates/
└── slipstream-server.service.j2  # slipstream systemd service
```

## License

GNU General Public License v3.0

## Author Information

This role is maintained by the [Unredacted](https://unredacted.org/) Team.
