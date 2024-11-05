# ansible-role-freesocks

An Ansible role for deploying and managing Outline VPN servers for [FreeSocks](https://freesocks.org/). This role supports both new server deployments and migrations between servers, with features including:

- Automated Outline server installation and configuration
- Cloudflare DNS record management with IPv4 and IPv6 support
- Cloudflare Tunnel setup for secure API access
- KV store integration for server management
- Multi-domain support
- Server migration capabilities

## Requirements

- Debian-based Linux system (tested on Debian only)
- Python 3.x
- Docker (will be installed by the role)
- Cloudflare account with:
  - API token with appropriate permissions
  - Account ID
  - Zone ID(s)
  - KV namespaces for API and Prometheus endpoints
  - Access Team configuration (for Prometheus access)

## Role Variables

### Required Variables

```yaml
# Cloudflare Configuration
cloudflare_api_endpoint: "https://api.cloudflare.com/client/v4"
cloudflare_email: "your-email@example.com"
cloudflare_api_token: "your-api-token"
cloudflare_account_id: "your-account-id"
cloudflare_zone_id: "your-zone-id"
cloudflare_api_kv_namespace: "your-api-kv-namespace"
cloudflare_prom_kv_namespace: "your-prom-kv-namespace"
cloudflare_access_team_name: "your-team-name"
cloudflare_access_aud_tag: "your-aud-tag"

# Domain Configuration
base_domain: "example.com"
api_domain: "api.example.com"
prom_domain: "prom.example.com"
kv_hostname_prefix: "outline"

# Envoy Mappings (for DNS records)
envoy_mappings:
  outline1-ams:
    ipv4:
      - "1.2.3.4"
      - "5.6.7.8"
    ipv6:
      - "2001:db8::1"
      - "2001:db8::2"
```

### Optional Variables

```yaml
# Operation mode (required but typically set in playbook)
operation_mode: "deploy"  # or "migrate"

# Server Configuration
outline_keys_port: 443
outline_api_port: 8443
cloudflared_os_version: "bookworm"  # OS codename for cloudflared package
random_hostname_length: 6
hostname_extension: ""  # Optional suffix for server names

# Additional Domains
additional_domains: []  # List of additional domains with optional zone_ids
# Example:
# additional_domains:
#   - domain: "example.app"
#     zone_id: "app-zone-id"  # Optional, defaults to base cloudflare_zone_id

# Migration Settings
migrate_delete_source: false  # Whether to delete source server KV entries after migration
source_hostname: ""          # Required for migration
destination_hostname: ""     # Required for migration
```

## Operation Modes

### Deploy Mode

Deploys a new Outline server with the following steps:
1. Verifies /opt/outline doesn't exist
2. Installs required packages and Docker
3. Installs and configures Outline server
4. Sets up DNS records (A and AAAA)
5. Configures Cloudflare Tunnel
6. Updates KV store with server information

### Migrate Mode

Migrates an existing Outline server to a new location:
1. Verifies source server exists in KV store
2. Verifies /opt/outline doesn't exist on destination
3. Installs new Outline server
4. Copies configuration from source to destination
5. Updates DNS and KV store entries
6. Optionally deletes source server KV entries

## Important Notes

- The role will fail early if /opt/outline already exists on the target system
- All Cloudflare credentials and configuration must be provided
- For migration, both source and destination hostnames are required
- IPv4 and IPv6 addresses can be provided through envoy_mappings or will fall back to server's own IPs

## Example Playbook

```yaml
# Deploy new server
- hosts: new_servers
  vars:
    operation_mode: deploy
    kv_hostname_prefix: "outline1-ams"
    # ... (other required variables)
  roles:
    - ansible-role-freesocks

# Migrate server
- hosts: new_servers
  vars:
    operation_mode: migrate
    source_hostname: "outline1-ams"
    destination_hostname: "outline2-fra"
    # ... (other required variables)
  roles:
    - ansible-role-freesocks
```

## Usage

```bash
# Deploy new server
ansible-playbook -i inventory playbook.yml \
  --extra-vars "operation_mode=deploy kv_hostname_prefix=outline1-ams"

# Migrate server
ansible-playbook -i inventory playbook.yml \
  --extra-vars "operation_mode=migrate source_hostname=outline1-ams destination_hostname=outline2-fra"
```

## License

GNU General Public License v3.0

## Author Information

This role is maintained by the [Unredacted](https://unredacted.org/) Team.
