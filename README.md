# ansible-role-freesocks

[![CI](https://github.com/unredacted/ansible-role-freesocks/actions/workflows/ci.yml/badge.svg)](https://github.com/unredacted/ansible-role-freesocks/actions/workflows/ci.yml)

An Ansible role for deploying and managing VPN servers for [FreeSocks](https://freesocks.org/). The role supports two backends:

- **Outline** — Shadowsocks proxy via the Outline `shadowbox` Docker container
- **Remnawave** — Xray-core node managed by an external [Remnawave Panel](https://docs.rw)

Both backends share the same role plumbing (DNS, control-plane registration, hostname rotation, migration) but are mutually exclusive per host. New deployments can target either; the eventual plan is to deprecate Outline.

Features:

- Automated installation and configuration of the chosen backend
- Pluggable provider architecture for DNS and CDN
- Cloudflare integration for DNS (origin A/AAAA records)
- Registration with the [FreeSocks Control Plane](https://freesocks.org/) (FCP) admin API
- Optional Fastly CDN fronting
- Hostname rotation for DNS blocking bypass
- Server migration between hosts
- Optional Remnawave Panel API integration (auto-fetch SECRET_KEY, auto-register node)

## Requirements

- Debian-based Linux system (tested on Debian 12)
- Python 3.x
- Docker (will be installed by the role)
- The `community.docker` Ansible collection (>=3.4.0). Install with:
  `ansible-galaxy collection install -r requirements.yml`
- Provider-specific requirements (see Provider Configuration)

## Quick start: brand-new deployment

This role provisions and fronts **one** proxy node and registers it with FCP. A first
deploy touches three systems **in order** — do the prerequisites before running the
role, or the run fails (or, if you skip Remnawave Phase 0, silently issues dead keys).

### 0. Prerequisites (do these first)

**Control plane (FCP)** — stand it up (see the FCP repo's deploy runbook), then:

- Seed tiers/settings and confirm the `member` tier exists.
- Mint a headless automation token on the FCP host and store the printed `fsv1_…`
  value in your vault as `fcp_api_token`:
  ```sh
  bunx convex run adminApi:mintAutomationToken \
    '{"name":"ansible","scopes":["admin:servers:read","admin:servers:write"]}'
  # admin:servers:write also covers the mode-placement (squad pool) binding.
  # add "admin:settings:write" only if you set fcp_default_connection_mode
  # add "admin:status:read"    only if you enable fcp_status_gate
  ```

**Remnawave panel** (Remnawave nodes only — the panel objects a working key needs:
a Config Profile + inbounds + an internal squad). Two ways to create them:

- **Automated (recommended) — `operation_mode=bootstrap`:** run the role once against
  the panel (host-agnostic, `delegate_to: localhost`) and it creates the Config Profile
  with the BASE WS/Reality inbounds (born compliant with the FreeSocks no-log Xray
  posture) and generates the Reality x25519 keypair. Squads are then created **per
  node** at deploy time (`remnawave_per_node_placement`, default on): each node clones
  the base inbound under its own tag, gets its own squad, and appends that squad to
  FCP's per-mode placement pools ("Beat censorship" / "Maximum privacy") — which is
  what lets FCP home each new key to the least-loaded node. Bootstrap PRINTS the
  created Config Profile + base inbound UUIDs; copy them into your node-deploy vars.
  Needs a panel API token and an `fsv1_` token with `admin:servers:write`:
  ```sh
  ansible-playbook playbook.yml --ask-vault-pass \
    --extra-vars "operation_mode=bootstrap remnawave_panel_url=https://panel.example.com fcp_enabled=true"
  ```
- **Manual:** in the panel UI create a Config Profile with your transport inbound(s) and
  the internal squad(s), then record each inbound's `configProfileInboundUuid`, the
  Config Profile UUID, and the squad UUID. Create a panel API token.
- **Skipping this leaves the node with no Hosts and no working keys — silently.**

**DNS** — a Cloudflare zone for your domain + a scoped API token (`Zone:DNS:Edit`).

**Target host** — a clean **Debian 12** box reachable over SSH as root (or a sudo user
with `--become`), with ports **80 and 443 free** (Caddy uses HTTP-01 on 80, TLS on 443).

**Controller** — ansible-core ≥ 2.15 and the collections:

```sh
ansible-galaxy collection install -r requirements.yml
```

### 1. Configure secrets + domains

Put every secret in an **Ansible Vault** — for a fronted Remnawave node that is
`cloudflare_api_token`, `fcp_api_token`, `remnawave_panel_api_token` (and/or
`remnawave_panel_secret_key`), and `fastly_api_token` (only if `cdn_provider: fastly`).

> The vault-decryption guard checks all of `cloudflare_api_token`, `fcp_api_token`,
> `remnawave_panel_api_token`, and `remnawave_panel_secret_key` for undecrypted vault
> blobs and fails fast. It still cannot catch a plain-text `your-…` placeholder — confirm
> every secret is filled before running.

Define your domain → provider map (in `group_vars` or the playbook):

```yaml
domain_providers:
  example.com:
    dns_provider: cloudflare
    cdn_provider: fastly # or 'none' for a Reality / unfronted node
    fastly_domain_mode: shared
    zone_id: "<cloudflare-zone-id>"
```

### 2. Deploy

Write a `playbook.yml` that applies the role to your host. A **fronted Remnawave node**:

```yaml
- hosts: new_node
  become: true
  vars_files: [vault.yml] # cloudflare_api_token, fcp_api_token, remnawave_panel_api_token, …
  vars:
    domain_providers: { } # as above (or in group_vars)
    outline_enabled: false
    remnawave_enabled: true
    remnawave_panel_url: "https://panel.example.org"
    remnawave_secret_key_source: "panel_api" # fetch SECRET_KEY from the panel
    remnawave_caddy_enabled: true
    remnawave_caddy_email: "admin@example.org"
    remnawave_panel_register_node: true
    remnawave_panel_config_profile_uuid: "<config-profile-uuid>"
    remnawave_cdn_transports:
      - { name: ws, network: ws, path: "/ws", internal_port: 8443, inbound_uuid: "<ws-inbound-uuid>", alpn: "http/1.1", fingerprint: chrome, enabled: true }
    fcp_enabled: true
    fcp_api_url: "https://control-plane.example.org"
    fcp_register_remnawave_panel: true # register the panel row (once)
    # per-node placement is ON by default: this deploy creates the node's own
    # inbound + squad and appends the squad to FCP's mode pools automatically
  roles:
    - ansible-role-freesocks
```

The operation mode is passed at run time; `--ask-vault-pass` decrypts the vault:

```sh
ansible-playbook -i inventory playbook.yml --ask-vault-pass \
  --extra-vars "operation_mode=deploy environment_mode=prod deploy_target_domain=example.com"
```

A minimal **Outline** node is simpler — Outline is the default backend and WSS/FCP are
opt-in; a play that leaves `outline_enabled: true` and sets `fcp_enabled: true` plus the
FCP + Cloudflare vars deploys an Outline server with the same run command.

`example-playbook.yml` has ready-to-adapt plays for every component combination; the
sections below document each variable.

### 3. Verify it worked

- **FCP admin dashboard** → the backend row for this host appears and reads healthy (the
  deploy already hard-asserts FCP could reach it unless `fcp_verify_connectivity=false`).
- **Remnawave panel** → the node is listed + online, with one Host per enabled transport.
- **Decoy** → `curl -sI https://<hostname>/` returns the camouflage site (HTTP 200) on any
  non-transport path.
- Issue a test key from FCP and confirm it connects.

## Provider Configuration

The role uses a pluggable provider architecture for DNS and CDN. Server endpoint data is published to the FreeSocks Control Plane (FCP) rather than to a key/value store (see [FreeSocks Control Plane Registration](#freesocks-control-plane-registration)).

| Provider | Options | Purpose |
|----------|---------|---------|
| `dns_provider` | `cloudflare` | DNS record management (origin A/AAAA records) |
| `cdn_provider` | `cloudflare`, `fastly`, `none` | Optional CDN/WSS fronting |

### Cloudflare Provider Requirements

When using the Cloudflare DNS provider, you need:
- API token with DNS edit permissions
- Account ID and Zone ID(s)

> **Removed:** Cloudflare Tunnel (`cloudflared`) and Cloudflare KV are no longer used by this role. The previous `tunnel_provider` and `kv_provider` options, the `cloudflare_*_kv_namespace_*` variables, and the Access Team / AUD-tag tunnel settings have all been removed. Origin DNS via Cloudflare is unchanged; server endpoint data is now pushed to FCP.

## Role Variables

### Required Variables

```yaml
# Provider Configuration (required via --extra-vars)
dns_provider: "cloudflare"

# Environment Mode (required via --extra-vars)
# NOTE: environment_mode is retained for compatibility but no longer selects
# KV namespaces (KV has been removed). It is effectively cosmetic.
environment_mode: "prod"  # or "dev"

# Operation Mode
operation_mode: "deploy"  # deploy | migrate | change | update | bootstrap
# bootstrap = one-time, panel-only: create the Remnawave Config Profile +
# inbounds + squads and bind them to FCP connection profiles (no host touched).

# Cloudflare Configuration (for the DNS provider)
cloudflare_api_endpoint: "https://api.cloudflare.com/client/v4"
cloudflare_api_token: "your-api-token"  # API Token (not Global API Key)
cloudflare_account_id: "your-account-id"
cloudflare_zone_id: "your-zone-id"

# FreeSocks Control Plane (FCP) registration — replaces Cloudflare KV
fcp_enabled: true
fcp_api_url: "https://fcp.example.org"
fcp_api_token: "your-fcp-fsv1-token"   # vault-encrypted fsv1_ service token

# Domain Configuration
# Zone IDs are looked up from domain_providers based on the domain
# Optionally include per-domain credentials for multi-account setups
domain_providers:
  example.com:
    dns_provider: cloudflare
    zone_id: "your-zone-id-for-com"
  example.app:
    dns_provider: cloudflare
    zone_id: "your-zone-id-for-app"
  # Domain on a different Cloudflare account
  other-domain.com:
    dns_provider: cloudflare
    zone_id: "zone-id-for-other-account"
    # Override credentials for this domain
    cloudflare_api_token: "your-api-token-for-other-account"
    cloudflare_account_id: "account-id-for-other-account"

# Active domain for operations (typically set via deploy_target_domain or change_target_domain)
base_domain: "example.com"
api_domain: "example.com"
prom_domain: "example.com"

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
source_hostname: ""
destination_hostname: ""
```

### FreeSocks Control Plane Registration

The role publishes each server's connection details to the **FreeSocks Control Plane (FCP)** instead of writing to a key/value store. FCP is a self-hosted [Convex](https://www.convex.dev/) backend (previously Cloudflare Workers + KV); it no longer reads KV. Instead it stores each server's management credential and dials **out** to the server.

Registration is a single **idempotent upsert keyed by `slug`**:

- `PUT /api/v1/admin/backend-servers/by-slug/{slug}` — creates the server record, or merges into the existing one (keep-secret-on-blank), returning `{created}`. No GET-list or client-side id resolution, so a re-run never clashes.
- After registering, the role optionally probes `POST …/backend-servers/test-connection` so a mistyped or unreachable backend fails the play loudly instead of leaving a dead row (gated by `fcp_verify_connectivity`, default `true`).

Requests use an `fsv1_` service token with `admin:servers:write` (plus `admin:servers:read` for the test-connection probe). Mint it headlessly on the FCP host:

    bunx convex run adminApi:mintAutomationToken '{"scopes":["admin:servers:read","admin:servers:write"]}'

This returns a scoped token attributed to a synthetic, credential-less `automation` admin (it can never sign in) — or create one in the FCP admin CMS → API Tokens.

```yaml
# Enable FCP registration (default: false)
fcp_enabled: true

# FCP admin API base URL
fcp_api_url: "https://fcp.example.org"

# fsv1_ service token with admin:servers:read + admin:servers:write scopes (store in vault!)
fcp_api_token: "fsv1_..."

# Display name for this server in FCP (optional)
fcp_server_name: ""

# Unique slug used for idempotent create/update (defaults to the random hostname)
fcp_server_slug: ""

# Remnawave only: register the PANEL with FCP (default: false)
fcp_register_remnawave_panel: false

# Slug for the Remnawave panel record in FCP
fcp_remnawave_panel_slug: "remnawave-primary"

# Optional capacity/ordering hints on the backend-servers row (empty = not sent)
fcp_max_keys: ""                     # cap issuance onto this instance ("null" clears)
fcp_priority: ""                     # pool ordering (lower = preferred)

# Opt-in post-deploy gate: poll GET /api/v1/admin/status until this slug reports
# healthy (needs admin:status:read; FCP's healthcheck cron runs every ~10 min).
fcp_status_gate: false
```

**What gets registered depends on the backend:**

- **Outline** — each server is one FCP `backendServers` row. The role registers the Caddy-proxied management `apiUrl` (which must present a **valid public TLS cert** — FCP rejects self-signed certs), plus `websocketEnabled` and `websocketDomain` (the Fastly edge domain when fronted, otherwise the origin hostname).

  > Because FCP requires a valid-TLS `apiUrl`, an Outline server registered with FCP needs the **Caddy API proxy**. That proxy is configured automatically when `outline_wss_enabled=true`, or via the no-WSS path that runs when `fcp_enabled=true`.

  > **Fixed WSS paths:** With WSS + FCP, the role forces the WSS listener paths to `/tcp` + `/udp` to match FCP's fixed client paths (see [WebSocket (WSS) Support](#websocket-wss-support)).

- **Remnawave** — FCP stores only the **panel** (`baseUrl` + `apiToken`), **not** individual nodes. Per-node config reaches users through the panel's subscription output. The role still registers the node and creates Hosts on the panel (separate, unchanged behavior). Registering the panel with FCP is opt-in via `fcp_register_remnawave_panel: true` (with `fcp_remnawave_panel_slug`).

**Node placement (per-mode squad pools).** FCP no longer binds squads to tiers — it homes each new key to the **least-loaded node** of the chosen connection mode's squad POOL, bound via `PATCH /api/v1/admin/remnawave/mode-placements` (scope `admin:servers:write`, the same one registration uses). With `remnawave_per_node_placement: true` (the default) every node deploy creates a per-node inbound + squad and **appends itself** to the right pool (`addSquadUuids` — never disturbing the rest); retiring a node runs `tasks/providers/remnawave/teardown_node_placement.yml`, which detaches it (`removeSquadUuids`), deletes the squad, and strips its inbounds from the Config Profile. Squad UUIDs are treated as sensitive (requests are `no_log`; FCP validates them server-side, audits only a `poolBound` boolean + pool size, and never echoes them back). The legacy single-unit topology (one panel-wide squad per mode, bound at bootstrap) remains available via `remnawave_bootstrap_shared_squads: true`.

**Migrate cleans up the source row.** When an Outline server is migrated to a new host, the destination is registered under its own slug and the role then **deletes the source server's FCP `backendServers` row** (by the source slug, `source_kv_hostname`) via `tasks/providers/fcp/delete_server.yml` — a single idempotent `DELETE …/backend-servers/by-slug/{slug}` (no GET-list / id resolution; no-ops if absent). This leaves no orphaned row behind, and is skipped when source and destination slugs are identical.

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

> **FCP-managed Outline forces `/tcp` + `/udp`:** FCP issues Outline WSS keys with
> **fixed** client paths (`/tcp` and `/udp`) and has no per-server path field. So when
> both `fcp_enabled=true` and `outline_wss_enabled=true`, the role overrides the WSS
> listener paths to `/tcp` + `/udp` and disables path randomization (logged at run
> time) — otherwise issued keys would point at random paths Caddy isn't serving.
> `outline_wss_random_paths` / `outline_wss_tcp_path` / `outline_wss_udp_path` only
> take effect on a non-FCP Outline deploy. (A configurable path field is a possible
> future FCP enhancement.)

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


### Remnawave Node Support

Deploy a Remnawave (Xray-core) node managed by an external Remnawave Panel. The role only deploys the node container and its surrounding plumbing — all proxy and user configuration is pushed by the panel at runtime.

```yaml
# Component flag (mutually exclusive with outline_enabled)
remnawave_enabled: true
outline_enabled: false

# Image and runtime
remnawave_node_image: "remnawave/node:2.7.0"      # override via --extra-vars for upgrades
remnawave_node_port: 2222                          # Panel ↔ Node API port
remnawave_node_install_dir: "/opt/remnanode"
remnawave_log_dir: "/var/log/remnanode"

# Panel connection (always required)
remnawave_panel_url: "https://panel.example.com"

# Where SECRET_KEY comes from
#   "vault"     — vault-encrypted variable below (default)
#   "panel_api" — role calls GET {panel_url}/api/keygen
remnawave_secret_key_source: "vault"
remnawave_panel_secret_key: "<vault-encrypted base64 payload>"

# Panel API integration (only required when secret_key_source=panel_api or
# when remnawave_panel_register_node=true)
remnawave_panel_api_token: "<vault-encrypted admin token>"

# Auto-register the node entry on the panel via POST /api/nodes (opt-in)
remnawave_panel_register_node: false
remnawave_panel_config_profile_uuid: "<config-profile-uuid>"
remnawave_panel_active_inbounds:
  - "<inbound-uuid-1>"
remnawave_panel_country_code: "US"

# Standalone Caddy on the host (TLS terminator + WS reverse-proxy)
# Required for Fastly fronting and for VLESS-TLS / Trojan-TLS transports.
# Reality transport doesn't need this.
remnawave_caddy_enabled: false
remnawave_caddy_email: "admin@example.com"
remnawave_caddy_listen_port: 443
# DEPRECATED alias — the Caddyfile now path-routes per remnawave_cdn_transports.
# Kept so stale references resolve; equals the `ws` transport's internal_port.
remnawave_caddy_xray_internal_port: 8443

# CDN transports (data-driven Caddy + Fastly VCL + Hosts). See the
# "CDN transports (WebSocket)" section above.
remnawave_cdn_transports:
  - { name: ws, network: ws, path: "/ws", internal_port: 8443, inbound_uuid: "", alpn: "http/1.1", fingerprint: chrome, enabled: true }

# Decoy/camouflage site served by Caddy on every non-transport path
remnawave_decoy_root: "/var/www/decoy"

# Reality transport (VLESS+Vision+REALITY — direct, no Caddy/CDN). See the
# "Reality transport" section below. Mutually exclusive with remnawave_caddy_enabled
# and incompatible with Fastly; keys live on the panel inbound (role creates the Host).
remnawave_reality_enabled: false
remnawave_reality_inbound_uuid: ""   # configProfileInboundUuid of the Reality inbound
remnawave_reality_sni: ""            # a serverName from the inbound (the borrowed domain)
remnawave_reality_address: ""        # client-facing address; defaults to dns_hostname
remnawave_reality_port: 443
remnawave_reality_fingerprint: "chrome"
```

#### Two workflows

**Panel UI workflow (default — simplest)**

1. In the Panel UI, click `Nodes` → `Management` → `+`. Fill in the node form.
2. Copy the generated `SECRET_KEY` value into Ansible vault as `remnawave_panel_secret_key`.
3. Run the role with `remnawave_enabled=true` and `remnawave_panel_url` set. The role deploys the container with the supplied key. After the container is up, finish the Panel UI flow by selecting a Config Profile and clicking `Create`.

**Panel API workflow (opt-in — fully automated)**

1. Create a long-lived API token via Panel UI → Tokens. Store as `remnawave_panel_api_token` in vault.
2. Set `remnawave_secret_key_source: panel_api` (so the role fetches the key automatically) and/or `remnawave_panel_register_node: true` (so the role creates the panel-side node entry).
3. When `register_node` is true, also supply `remnawave_panel_config_profile_uuid` and `remnawave_panel_active_inbounds`. The role calls `POST /api/nodes`, captures the returned UUID, and persists it locally to `/opt/remnanode/.node_uuid` for use by future change/migrate flows (which then `PATCH /api/nodes` to keep the panel in sync with the new hostname).

#### Caddy + Fastly coordination

Remnawave's Xray runs in `network_mode: host`, so it shares ports with the rest of the host. When Fastly fronts the node, Fastly's origin needs a real public TLS cert at the node — which means **standalone Caddy on the host** (not embedded like Outline's Caddy). The Caddy install:

- Listens on `remnawave_caddy_listen_port` (default 443) — terminates TLS with a Let's Encrypt cert
- Auto-issues the cert via HTTP-01 (port 80 must be free at provisioning time and during renewal)
- Reverse-proxies all traffic — including WebSocket upgrades — to `127.0.0.1:remnawave_caddy_xray_internal_port` (default 8443)

The panel-side Xray inbound configuration must match: bind on the internal port (e.g. `127.0.0.1:8443`) with **plaintext WebSocket** transport. TLS termination is handled by Caddy, not Xray.

#### CDN transports (WebSocket)

When the node is fronted by Fastly, the role models its CDN-fronted Xray transports as a **data-driven list**, `remnawave_cdn_transports`. Each entry corresponds to one panel Xray inbound, one Caddy path-route, one Fastly VCL rule, and one Remnawave Host — all driven from a single place so they can't drift.

```yaml
remnawave_cdn_transports:
  - name: ws
    network: ws                 # ws
    path: "/ws"                 # PANEL-GLOBAL — must equal the inbound's wsSettings.path
    internal_port: 8443         # must equal the inbound's listen port
    inbound_uuid: ""            # configProfileInboundUuid from the Config Profile
    alpn: "http/1.1"
    fingerprint: chrome
    enabled: true

# Decoy/camouflage site served on every non-transport path
remnawave_decoy_root: "/var/www/decoy"
```

**Path is panel-global, not per-node-random.** Unlike Outline's WSS random paths, each transport's `path` must exactly equal the `wsSettings.path` of the matching inbound in the panel's shared Config Profile. Every node attached to that inbound uses the same path. Likewise `internal_port` must equal the inbound's `listen` port, and `inbound_uuid` is the `configProfileInboundUuid`.

**Caddy + decoy site.** Caddy path-routes the `ws` transport to its loopback inbound via an exact-path `handle`, and serves a plausible static "maintenance" page (rooted at `remnawave_decoy_root`) on all other paths. A probe of `https://<host>/` therefore sees an innocuous site rather than a bare proxy or error.

**Automatic Host creation (Fastly and direct).** On a Remnawave deploy with node registration enabled, the role creates **one Remnawave Host per enabled transport** (`POST /api/hosts`, idempotent: it lists existing Hosts first and matches by `remark`). Hosts are what put a node's keys into client subscriptions — without them, a registered node never appears in subscription output. The Host's `address`/`sni`/`host` is the **Fastly edge domain when fronted, otherwise the origin `dns_hostname`** (`fastly_websocket_domain | default(dns_hostname)`) — Caddy already terminates a real Let's Encrypt cert on that origin and path-routes to the inbounds, so a direct (non-Fastly) node is a complete, directly-reachable endpoint (e.g. `wss://<dns_hostname>/ws`). Port is 443 with `securityLayer: TLS`. Hosts are gated on `remnawave_enabled` + `remnawave_panel_register_node` + `remnawave_caddy_enabled` + a `cdn_provider` of `fastly` **or** `none`; `remnawave_panel_active_inbounds` is **derived** from the enabled transports' `inbound_uuid`s, and hostname rotation / migration deletes the old node's Hosts (matched by remark prefix) before creating new ones.

**Phase 0 — one-time manual panel prerequisite** (skipped by `operation_mode=bootstrap`, which creates this for you). The operator adds the WS raw-Xray inbound to the panel's Config Profile:

- **WS** — tag `VLESS_WS_CDN`, `listen 127.0.0.1`, `port 8443`, `network: ws`, `security: none`, `wsSettings.path: /ws`.

Then record its UUID (from `GET /api/config-profiles/inbounds`) into the matching `remnawave_cdn_transports[].inbound_uuid`.

**Why WS only (no XHTTP).** XHTTP was removed 2026-07-04: Fastly cannot relay XHTTP's long-lived streamed download — its `do_stream` is a cache feature ("Streaming Miss") that explicitly forbids endless responses, and Fastly staff confirmed the equivalent long-lived gRPC stream is impossible via VCL (only via Fastly Compute). Cloudflare fronting is off the table (VPN fronting is banned there). WS-over-Fastly is a first-class upgrade tunnel — the shape Fastly supports — and is the fronted transport.

#### Reality transport (VLESS+Vision+REALITY — direct, no Caddy/CDN)

Reality is a separate **direct** node mode (not part of `remnawave_cdn_transports`). Xray terminates TLS itself by borrowing a real site's handshake, so it binds `:443` directly with no Caddy and no CDN. Enable it with:

```yaml
remnawave_enabled: true
outline_enabled: false

remnawave_caddy_enabled: false           # MUST stay off — Reality binds :443 itself
remnawave_reality_enabled: true
remnawave_reality_inbound_uuid: "<configProfileInboundUuid of the Reality inbound>"
remnawave_reality_sni: "www.some-real-site.com"   # a serverName from the inbound (borrowed domain)
# remnawave_reality_address: ""          # client-facing address; defaults to dns_hostname
# remnawave_reality_port: 443
# remnawave_reality_fingerprint: chrome

remnawave_panel_register_node: true       # role creates the Reality Host
remnawave_panel_url: "https://panel.example.com"
remnawave_panel_api_token: "<vault>"
remnawave_panel_config_profile_uuid: "<config-profile-uuid>"
```

**Constraints (asserted by the role).** Reality is **mutually exclusive with `remnawave_caddy_enabled`** (both would bind `:443`) and **incompatible with Fastly**. The role asserts these and forces `dns_proxied: false` (a Reality endpoint must be reached directly).

**Keys live on the panel, not the role.** The Reality `publicKey` / `shortIds` / `privateKey` / `flow` are configured on the **panel inbound** (raw Xray config). The role only creates the Remnawave Host (`tasks/providers/remnawave/create_reality_host.yml`): remark `<hostname>-reality`, `address`/`sni` from `remnawave_reality_address` (default `dns_hostname`) / `remnawave_reality_sni`, `securityLayer: DEFAULT` — the panel derives `pbk`/`sid`/`flow` from the inbound. The Reality inbound's UUID is also added to the derived `remnawave_panel_active_inbounds` when enabled. Reality is wired into deploy / change / migrate (gated on `remnawave_reality_enabled` + `remnawave_panel_register_node`).

**Phase 0 (Reality) — one-time manual panel prerequisite.** Add a Reality inbound to the Config Profile — raw Xray: `vless`, `listen 0.0.0.0:443`, `security reality`, `realitySettings { dest, serverNames, privateKey, shortIds }`, `flow xtls-rprx-vision` — generate the x25519 keypair (panel UI / `GenerateX25519` endpoint / `xray x25519`), then record the inbound UUID (from `GET /api/config-profiles/inbounds`) into `remnawave_reality_inbound_uuid` and one serverName into `remnawave_reality_sni`.

#### Choosing a transport

| Transport | Setup | Strength | Best for |
|-----------|-------|----------|----------|
| **VLESS+WS+TLS** (via Caddy, real LE cert) | `remnawave_caddy_enabled` + `ws` transport | Rides ordinary HTTPS — traverses forced proxies; TLS inspection sees normal HTTPS (blocking it means blocking all HTTPS) | **Business / school networks** (forced proxies, TLS inspection) |
| **VLESS+Vision+REALITY** (direct) | `remnawave_reality_enabled` | Fastest; best against active probing (raw TCP, mimics a real site's TLS) | **Open networks** |

The tradeoff in one line: **WS+TLS via Caddy** is the choice for restrictive **business/school** networks because it looks like — and rides — normal HTTPS, so it gets through forced HTTP proxies and TLS inspection. **Reality** is the **fastest** option and the strongest against active probing, but it does **not** traverse forced HTTP proxies / TLS inspection, so it is for **open** networks, not the business/school case.

#### Notes

- **slipstream coexistence**: `slipstream_enabled=true` with `slipstream_mode=raw` (microsocks-backed) works alongside Remnawave. `slipstream_mode=shadowsocks` requires Outline.
- **FCP registers the panel, not the node**: For Remnawave, FCP stores only the panel (`baseUrl` + `apiToken`); per-node config reaches users via the panel's subscription output. Set `fcp_register_remnawave_panel: true` to register the panel with FCP (see [FreeSocks Control Plane Registration](#freesocks-control-plane-registration)).
- **TLS certificates for non-Caddy setups**: If you don't enable `remnawave_caddy_enabled`, you're responsible for either using Reality (no public cert needed) or mounting your own cert files into the container at `/var/lib/remnawave/configs/xray/ssl/`.


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

# Remnawave node (Panel UI workflow — paste SECRET_KEY into vault first)
ansible-playbook playbook.yml --ask-vault-pass \
  --extra-vars "operation_mode=deploy environment_mode=prod" \
  --extra-vars "deploy_target_domain=example.com" \
  --extra-vars "outline_enabled=false remnawave_enabled=true" \
  --extra-vars "remnawave_panel_url=https://panel.example.com"

# Remnawave node (Panel API workflow — role fetches SECRET_KEY + registers node)
ansible-playbook playbook.yml --ask-vault-pass \
  --extra-vars "operation_mode=deploy environment_mode=prod" \
  --extra-vars "deploy_target_domain=example.com" \
  --extra-vars "outline_enabled=false remnawave_enabled=true" \
  --extra-vars "remnawave_panel_url=https://panel.example.com" \
  --extra-vars "remnawave_secret_key_source=panel_api remnawave_panel_register_node=true" \
  --extra-vars "remnawave_panel_config_profile_uuid=<uuid>" \
  --extra-vars "remnawave_panel_country_code=US"

# Remnawave node + standalone Caddy (Fastly fronting / VLESS-TLS support)
ansible-playbook playbook.yml --ask-vault-pass \
  --extra-vars "operation_mode=deploy environment_mode=prod" \
  --extra-vars "deploy_target_domain=example.com" \
  --extra-vars "outline_enabled=false remnawave_enabled=true" \
  --extra-vars "remnawave_panel_url=https://panel.example.com" \
  --extra-vars "remnawave_caddy_enabled=true remnawave_caddy_email=admin@example.com"

# Remnawave + slipstream raw (independent transports)
ansible-playbook playbook.yml --ask-vault-pass \
  --extra-vars "operation_mode=deploy environment_mode=prod" \
  --extra-vars "deploy_target_domain=example.com" \
  --extra-vars "outline_enabled=false remnawave_enabled=true" \
  --extra-vars "remnawave_panel_url=https://panel.example.com" \
  --extra-vars "slipstream_enabled=true slipstream_mode=raw slipstream_base_domain=your-dns.com"
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

# Keep old DNS records
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=change environment_mode=prod" \
  --extra-vars "change_target_domain=example.app" \
  --extra-vars "change_delete_old_dns=false"
```

### Migrate Mode Examples

```bash
# Migrate server to new host
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=migrate environment_mode=prod" \
  --extra-vars "source_hostname=old-server source_kv_hostname=apple-banana" \
  --extra-vars "destination_hostname=new-server destination_kv_hostname=apple-banana" \
  --extra-vars "dns_provider=cloudflare"
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

# Re-pull and recreate a Remnawave node (e.g. to pick up a new image tag)
ansible-playbook playbook.yml --ask-vault-pass \
  --extra-vars "operation_mode=update environment_mode=prod" \
  --extra-vars "outline_enabled=false remnawave_enabled=true" \
  --extra-vars "remnawave_panel_url=https://panel.example.com" \
  --extra-vars "remnawave_node_image=remnawave/node:2.7.1" \
  --extra-vars "force_reinstall_remnawave=true"
```

### Component Flags Reference

| Flag | Default | Description |
|------|---------|-------------|
| `outline_enabled` | `true` | Deploy Outline Shadowsocks server (mutually exclusive with `remnawave_enabled`) |
| `outline_wss_enabled` | `false` | Enable WebSocket transport (requires Outline) |
| `remnawave_enabled` | `false` | Deploy Remnawave Xray-core node (mutually exclusive with `outline_enabled`) |
| `remnawave_panel_register_node` | `false` | Auto-register node on the Panel via `POST /api/nodes` |
| `remnawave_caddy_enabled` | `false` | Install standalone Caddy (TLS terminator + WS proxy) — required for Fastly fronting |
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
6. Registers the server with FCP (if `fcp_enabled`)

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
1. Verifies the source server state
2. Verifies /opt/outline doesn't exist on destination
3. Installs new Outline server
4. Copies configuration from source to destination
5. Updates DNS records for the destination
6. Updates the FCP registration to point at the destination (if `fcp_enabled`)

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
10. Updates the FCP registration with the new endpoint (if `fcp_enabled`)
11. Optionally deletes old DNS records

**Required Configuration:**
```yaml
# In your playbook vars
domain_providers:
  example.com:
    dns_provider: cloudflare
    zone_id: "your-zone-id-for-com"
  example.app:
    dns_provider: cloudflare
    zone_id: "your-zone-id-for-app"
  # Future providers (framework ready)
  # example.org:
  #   dns_provider: fastly
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

Adds new components (slipstream, WebSocket) to an **existing** server without reinstalling Outline. Automatically detects the existing hostname and installed components, and re-syncs the server's FCP registration.

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
4. Re-syncs the server's FCP registration with the new component configuration (if `fcp_enabled`)

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
│   ├── outline_api_proxy.yml # Caddy API proxy (no-WSS path for FCP)
│   ├── websocket.yml        # WebSocket (WSS) configuration
│   ├── docker.yml           # Docker CE install (Remnawave)
│   ├── remnawave.yml        # Remnawave node container deployment
│   ├── caddy.yml            # Standalone Caddy (Remnawave) + decoy
│   ├── decoy.yml            # Decoy/camouflage web root + index page
│   └── slipstream.yml       # slipstream DNS tunnel setup
├── change/
│   ├── change.yml           # Backend-detect dispatcher
│   ├── outline_change.yml   # Outline hostname rotation
│   └── remnawave_change.yml # Remnawave hostname rotation
├── migrate/
│   ├── migrate.yml          # Migration orchestration
│   ├── transfer_config.yml  # Outline config transfer
│   ├── transfer_remnawave.yml # Remnawave config transfer
│   └── containers.yml       # Container management
├── update/
│   └── remnawave_update.yml # Update mode for Remnawave hosts
└── providers/
    ├── cloudflare/
    │   ├── dns.yml          # DNS management (origin A/AAAA records)
    │   └── migrate/         # Migration-specific DNS tasks
    ├── fastly/              # Fastly CDN service / TLS / DNS / cleanup
    ├── remnawave/
    │   ├── keygen.yml             # GET /api/keygen → SECRET_KEY
    │   ├── register_node.yml      # POST /api/nodes (captures UUID)
    │   ├── update_node.yml        # PATCH /api/nodes
    │   ├── delete_node.yml        # DELETE /api/nodes/{uuid}
    │   ├── create_hosts.yml       # POST /api/hosts per enabled CDN transport
    │   ├── create_reality_host.yml # POST /api/hosts for the Reality node
    │   └── cleanup_hosts.yml      # DELETE old Hosts (by remark prefix)
    └── fcp/                 # FreeSocks Control Plane registration (REST API)
        ├── register_server.yml          # Outline backendServers row
        ├── register_remnawave_panel.yml # Remnawave panel record
        └── delete_server.yml            # DELETE a backendServers row by slug (migrate)

templates/
├── slipstream-server.service.j2     # slipstream systemd service
├── remnanode-docker-compose.yml.j2  # Remnawave compose
├── remnanode-caddyfile.j2           # Multi-transport Caddy + decoy fallback
├── remnanode-logrotate.j2           # Remnawave log rotation
└── decoy-index.html.j2              # Decoy/camouflage landing page
```

> The `cloudflare/` provider no longer contains `install.yml` (cloudflared), `tunnel.yml`, or `kv.yml` — Cloudflare Tunnel and KV have been removed.

## Testing

Every push and PR runs three CI jobs (`.github/workflows/ci.yml`): **lint**
(YAML parse + playbook syntax checks), **unit** (offline expression tests), and
**integration** — a full end-to-end run against a **real Remnawave panel**.

**Unit tests** (offline, `connection: local`, no hosts or APIs):

- **`tests/test_bootstrap.yml`** — the bootstrap/placement filter expressions:
  FCP mode-placement bodies (full-replace + per-node `addSquadUuids`), the
  per-node inbound plan + clone logic (incl. re-run idempotency), x25519/response
  shape tolerance.
- **`tests/test_caddyfile_render.yml`** — renders the Remnawave Caddyfile
  template and asserts its structure (transport routes + decoy fallback).
- **`tests/test_fcp_and_hosts.yml`** — the FCP Outline `apiUrl` construction and
  the Remnawave Host request-body shaping.

```bash
tests/run.sh                                   # every tests/test_*.yml
ansible-playbook tests/test_bootstrap.yml      # or one at a time
```

**Integration harness** (`tests/run_integration.sh`) — stands up an ephemeral
**Remnawave panel** (`tests/panel/`, pinned to the fleet's release, fresh DB
every run), a **contract-strict mock FCP** (`tests/mock_fcp.py` — validates
requests exactly like the real control plane, including Convex-style
undeclared-field rejection and squad-UUID checks), and a **mock Cloudflare API**
(`tests/mock_cloudflare.py`), then runs two playbooks:

1. **`tests/test_integration.yml`** — task-level: `operation_mode=bootstrap`
   (asserts the profile is born with the no-log Xray posture), per-node
   placement for two simulated nodes (idempotent re-runs, the duplicate
   inbound-port case, append-only FCP pools), and teardown (pool detach, squad
   delete, inbound strip — the other node untouched).
2. **`tests/test_deploy.yml`** (with `RUN_DEPLOY_PHASE=1`) — the **real
   `operation_mode=deploy` path**, exactly as production runs it: validation,
   hostname, (mock) DNS, apt installs, a **real `remnawave/node` container**
   the panel actually connects to over the node port, per-node Reality
   placement, the Reality Host, and FCP registration + placement pools + the
   status gate. A Reality node is used because it is the one production
   topology needing no public TLS issuance.

```bash
bash tests/run_integration.sh                     # task-level phases (any OS)
RUN_DEPLOY_PHASE=1 bash tests/run_integration.sh  # + the real deploy (Linux + passwordless sudo)
```

Requires Docker, ansible-core ≥ 2.15, and python3. Both mocks are ephemeral and
everything is torn down on exit (including `/opt/remnanode` from the deploy
phase). CI always runs the deploy phase.

A syntax check of the example playbook is also useful:

```bash
ansible-playbook --syntax-check example-playbook.yml
```

## License

GNU General Public License v3.0

## Author Information

This role is maintained by the [Unredacted](https://unredacted.org/) Team.
