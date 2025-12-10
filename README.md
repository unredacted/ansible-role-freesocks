# ansible-role-freesocks

An Ansible role for deploying and managing Outline VPN servers for [FreeSocks](https://freesocks.org/). This role supports both new server deployments and migrations between servers, with features including:

- Automated Outline server installation and configuration
- Pluggable provider architecture for DNS, tunnel, and KV store
- Cloudflare integration (DNS, Tunnel, KV) with more providers coming soon
- Multi-domain support
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
cloudflare_email: "your-email@example.com"
cloudflare_api_token: "your-api-token"
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
base_domain: "example.com"
api_domain: "api.example.com"
prom_domain: "prom.example.com"
kv_hostname_prefix: "outline"

# Envoy Mappings (for DNS records)
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
random_hostname_length: 6
hostname_extension: ""

# Additional Domains
additional_domains: []

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

# WebSocket paths (defaults shown)
outline_wss_tcp_path: "/tcp"
outline_wss_udp_path: "/udp"
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

## Usage

All operations require explicit provider and environment mode settings:

```bash
# Deploy with Cloudflare (full stack)
ansible-playbook -i inventory playbook.yml \
  --extra-vars "operation_mode=deploy environment_mode=prod dns_provider=cloudflare tunnel_provider=cloudflare kv_provider=cloudflare"

# Deploy without tunnel (direct port access)
ansible-playbook -i inventory playbook.yml \
  --extra-vars "operation_mode=deploy environment_mode=prod dns_provider=cloudflare tunnel_provider=none kv_provider=cloudflare"

# Migrate server
ansible-playbook -i inventory playbook.yml \
  --extra-vars "operation_mode=migrate environment_mode=prod dns_provider=cloudflare tunnel_provider=cloudflare kv_provider=cloudflare source_hostname=outline1-ams destination_hostname=outline2-fra"

# Change hostname (when current hostname is blocked)
ansible-playbook -i inventory playbook.yml \
  --extra-vars "operation_mode=change environment_mode=prod dns_provider=cloudflare tunnel_provider=cloudflare kv_provider=cloudflare"
```

## Operation Modes

### Deploy Mode

Deploys a new Outline server:
1. Validates provider and environment configuration
2. Installs required packages (provider-specific)
3. Installs and configures Outline server
4. Sets up DNS records via configured provider
5. Configures tunnel (if `tunnel_provider != none`)
6. Updates KV store with server information

### Migrate Mode

Migrates an existing Outline server to a new location:
1. Verifies source server exists in KV store
2. Verifies /opt/outline doesn't exist on destination
3. Installs new Outline server
4. Copies configuration from source to destination
5. Updates DNS and KV store entries
6. Optionally deletes source server KV entries

### Change Mode

Rotates the hostname on an existing server when the current hostname is blocked by DNS filtering:

1. Reads existing API info from server
2. Generates new random hostname
3. Creates new DNS records
4. Updates Caddy domain configuration (triggers new TLS certificate)
5. Updates server hostname setting
6. Updates KV store with new endpoint
7. Optionally deletes old DNS and KV entries

**Settings:**
```yaml
# Whether to delete old DNS records after hostname change
change_delete_old_dns: true
# Whether to delete old KV entries after hostname change
change_delete_old_kv: true
```

## Directory Structure

```
tasks/
├── main.yml                 # Orchestrator with provider routing
├── setup/
│   ├── install.yml          # Base package installation
│   └── outline.yml          # Outline server setup
├── migrate/
│   ├── migrate.yml          # Migration orchestration
│   ├── transfer_config.yml  # Config transfer (provider-agnostic)
│   └── containers.yml       # Container management
└── providers/
    └── cloudflare/
        ├── install.yml      # Cloudflared installation
        ├── dns.yml          # DNS management
        ├── tunnel.yml       # Tunnel setup
        ├── kv.yml           # KV store operations
        └── migrate/         # Migration-specific tasks
```

## License

GNU General Public License v3.0

## Author Information

This role is maintained by the [Unredacted](https://unredacted.org/) Team.
