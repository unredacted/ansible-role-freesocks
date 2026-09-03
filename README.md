# ansible-role-freesocks

[![CI](https://github.com/unredacted/ansible-role-freesocks/actions/workflows/ci.yml/badge.svg)](https://github.com/unredacted/ansible-role-freesocks/actions/workflows/ci.yml)

An Ansible role for deploying and managing VPN servers for [FreeSocks](https://freesocks.org/). It supports two mutually-exclusive backends per host:

- **Outline** — Shadowsocks proxy via the Outline `shadowbox` Docker container
- **Remnawave** — Xray-core node managed by an external Remnawave Panel

Both share the same plumbing — DNS, control-plane registration, hostname rotation, migration. New deployments can target either; the long-term plan is to deprecate Outline.

**Features:** automated backend install; pluggable DNS/CDN providers (Cloudflare DNS, optional Fastly fronting); registration with the [FreeSocks Control Plane](https://freesocks.org/) (FCP); hostname rotation to bypass DNS blocking; server migration; optional Remnawave Panel API integration (auto-fetch SECRET_KEY, auto-register node).

## Requirements

- Debian-based Linux target (tested on Debian 12), Python 3.x, ports **80 and 443 free** (Caddy uses HTTP-01 on 80, TLS on 443)
- Docker (installed by the role)
- Controller: ansible-core ≥ 2.15 and the collections in `requirements.yml`:
  ```sh
  ansible-galaxy collection install -r requirements.yml
  ```

## Operation modes

Passed at run time via `operation_mode`:

| Mode | Purpose |
|------|---------|
| `deploy` | Install a new server with selected components on a random hostname |
| `change` | Rotate the hostname (optionally to a different domain) to bypass DNS blocking |
| `migrate` | Move an existing server to a new host (auto-detects backend and transport) |
| `update` | Add components to / re-sync an existing server |
| `bootstrap` | One-time, panel-only Remnawave setup (host-agnostic; see below) |

`environment_mode` (`prod`\|`dev`) is still required via `--extra-vars` but is now cosmetic — it no longer selects KV namespaces (KV was removed).

## Quick start (new deployment)

A first deploy touches three systems **in order**. Do the prerequisites first, or the run fails (or, if you skip Remnawave Phase 0, silently issues dead keys).

### 0. Prerequisites

**Control plane (FCP)** — stand it up, confirm the admin API is reachable, then mint a headless token on the FCP host and vault the printed `fsv1_…` value as `fcp_api_token`:

```sh
bunx convex run adminApi:mintAutomationToken \
  '{"name":"ansible","scopes":["admin:servers:read","admin:servers:write"]}'
# admin:servers:write also covers mode-placement (squad pool) binding.
# add "admin:settings:write" only if you set fcp_default_connection_mode
# add "admin:status:read"    only if you enable fcp_status_gate
```

**Remnawave panel** (Remnawave nodes only) — a working key needs a Config Profile + inbounds + a squad. Two ways to create them:

- **Automated (recommended)** — `operation_mode=bootstrap` runs the role once against the panel (`delegate_to: localhost`, no host touched). It creates the `FreeSocks-Config` Config Profile with the base WS / Reality / relay inbounds (born compliant with the no-log Xray posture), generates an x25519 keypair **per REALITY transport**, creates the three panel-wide squads (`FreeSocks-Fastly` for WS/CDN, `FreeSocks-Reality` for direct Reality, `FreeSocks-Relay` for REALITY behind an external L4 proxy), and binds them to FCP's per-mode placement pools (`freedom-ws` / `privacy-reality` / `freedom-reality`). It **prints** the Config Profile + inbound UUIDs to copy into your node-deploy vars. Re-running it reconciles missing base inbounds by tag.
  ```sh
  ansible-playbook playbook.yml --ask-vault-pass \
    --extra-vars "operation_mode=bootstrap remnawave_panel_url=https://panel.example.com fcp_enabled=true"
  ```
- **Manual** — create the Config Profile, inbound(s), and squad(s) in the panel UI, then record each inbound's `configProfileInboundUuid`, the Config Profile UUID, and the squad UUID, plus a panel API token.

> Skipping this leaves the node with no Hosts and no working keys — silently.

**DNS** — a Cloudflare zone + scoped API token (`Zone:DNS:Edit`).

**Target host** — a clean Debian 12 box reachable over SSH as root (or a sudo user with `--become`).

### 1. Secrets and domains

Put every secret in an **Ansible Vault**. For a fronted Remnawave node that is `cloudflare_api_token`, `fcp_api_token`, `remnawave_panel_api_token` (and/or `remnawave_panel_secret_key`), and `fastly_api_token` (only if `cdn_provider: fastly`).

> The vault-decryption guard fails fast on undecrypted vault blobs in those secrets, but cannot catch a plain-text `your-…` placeholder — confirm every secret is filled.

Define your domain → provider map (in `group_vars` or the playbook):

```yaml
domain_providers:
  example.com:
    dns_provider: cloudflare
    cdn_provider: fastly        # or 'none' for a Reality / relay / unfronted node
    fastly_domain_mode: shared
    zone_id: "<cloudflare-zone-id>"
```

### 2. Deploy

A **fronted Remnawave node** playbook:

```yaml
- hosts: new_node
  become: true
  vars_files: [vault.yml]
  vars:
    domain_providers: {}                       # as above (or in group_vars)
    outline_enabled: false
    remnawave_enabled: true
    remnawave_panel_url: "https://panel.example.org"
    remnawave_secret_key_source: "panel_api"   # fetch SECRET_KEY from the panel
    remnawave_caddy_enabled: true
    remnawave_caddy_email: "admin@example.org"
    remnawave_panel_register_node: true
    remnawave_panel_config_profile_uuid: "<config-profile-uuid>"
    remnawave_cdn_transports:
      - { name: ws, network: ws, path: "/ws", internal_port: 8443, inbound_uuid: "<ws-inbound-uuid>", alpn: "http/1.1", fingerprint: chrome, enabled: true }
    fcp_enabled: true
    fcp_api_url: "https://control-plane.example.org"
    fcp_register_remnawave_panel: true
    # the panel-wide squads were created and bound to FCP's mode pools at
    # bootstrap — this deploy only registers the node against the SHARED base
    # inbounds and creates its Hosts
  roles:
    - ansible-role-freesocks
```

```sh
ansible-playbook -i inventory playbook.yml --ask-vault-pass \
  --extra-vars "operation_mode=deploy environment_mode=prod deploy_target_domain=example.com"
```

A minimal **Outline** node is simpler: Outline is the default backend, WSS/FCP are opt-in. `example-playbook.yml` has ready-to-adapt plays for every component combination.

### 3. Verify

- **FCP dashboard** → the backend row for this host appears healthy (the deploy hard-asserts FCP reachability unless `fcp_verify_connectivity=false`).
- **Remnawave panel** → node listed + online, with one Host per enabled transport.
- **Decoy** → `curl -sI https://<hostname>/` returns the camouflage site (HTTP 200) on any non-transport path. (Not applicable to Reality/relay nodes, which serve no web root.)
- Issue a test key from FCP and confirm it connects.

## Provider configuration

| Provider | Options | Purpose |
|----------|---------|---------|
| `dns_provider` | `cloudflare` | DNS record management (origin A/AAAA) |
| `cdn_provider` | `cloudflare`, `fastly`, `none` | Optional CDN/WSS fronting |

All domain-specific config lives in `domain_providers`. Each entry needs `dns_provider` + `zone_id`; add `cdn_provider`, `fastly_domain_mode`, and per-domain `cloudflare_api_token`/`cloudflare_account_id` as needed for multi-account setups. Zone IDs are looked up from this map by the active domain — no separate zone variables.

> **Removed:** Cloudflare Tunnel (`cloudflared`) and Cloudflare KV. The `tunnel_provider`/`kv_provider` options, `cloudflare_*_kv_namespace_*` vars, and Access Team / AUD-tag settings are gone. Origin DNS via Cloudflare is unchanged; endpoint data now goes to FCP.

## Role variables

### Required (via `--extra-vars` or playbook)

```yaml
operation_mode: "deploy"        # deploy | change | migrate | update | bootstrap
environment_mode: "prod"        # or "dev" (cosmetic)
dns_provider: "cloudflare"

cloudflare_api_endpoint: "https://api.cloudflare.com/client/v4"
cloudflare_api_token: "..."     # API Token (not Global API Key)
cloudflare_account_id: "..."

fcp_enabled: true
fcp_api_url: "https://fcp.example.org"
fcp_api_token: "..."            # vault-encrypted fsv1_ token

domain_providers: {}            # see Provider configuration
base_domain: "example.com"      # usually set by deploy_target_domain / change_target_domain
```

Optionally, `envoy_mappings` maps an inventory host to explicit IPs for multi-IP DNS records:

```yaml
envoy_mappings:
  outline1-ams:
    ipv4: ["1.2.3.4", "5.6.7.8"]
    ipv6: ["2001:db8::1"]
```

### Common optional variables

```yaml
custom_hostname: "my-server"    # override auto-generated hostname
hostname_word_count: 3          # words in a random hostname (e.g. apple-banana-cherry)
dns_proxied: false              # true routes through the CDN proxy
outline_keys_port: 443          # move off 443 (e.g. 853) when WSS needs 443 for Caddy
```

## FreeSocks Control Plane (FCP) registration

The role publishes each server's connection details to FCP, a self-hosted [Convex](https://www.convex.dev/) backend that stores each server's management credential and dials **out** to it (no KV).

Registration is a single **idempotent upsert keyed by `slug`**:

- `PUT /api/v1/admin/backend-servers/by-slug/{slug}` — creates or merges (keep-secret-on-blank). No GET-list or id resolution, so re-runs never clash.
- Optionally probes `POST …/test-connection` afterward so a mistyped/unreachable backend fails loudly (`fcp_verify_connectivity`, default `true`).

Requests use an `fsv1_` token with `admin:servers:write` (+ `admin:servers:read` for the probe).

```yaml
fcp_enabled: true
fcp_api_url: "https://fcp.example.org"
fcp_api_token: "fsv1_..."             # vault!
fcp_server_name: ""                   # display name (default: random hostname)
fcp_server_slug: ""                   # idempotency key (default: random hostname)
fcp_register_remnawave_panel: false   # Remnawave: register the PANEL row
fcp_remnawave_panel_slug: "remnawave-primary"
fcp_max_keys: ""                      # optional issuance cap (empty = not sent)
fcp_priority: ""                      # optional pool ordering (lower = preferred)
fcp_status_gate: false                # poll GET /admin/status until healthy (admin:status:read)
```

**What gets registered:**

- **Outline** — one `backendServers` row: the Caddy-proxied management `apiUrl` (must present a **valid public TLS cert** — FCP rejects self-signed), plus `websocketEnabled` and `websocketDomain` (Fastly edge when fronted, else origin hostname). This requires the Caddy API proxy, configured automatically when `outline_wss_enabled=true` or via the no-WSS path that runs when `fcp_enabled=true`.
- **Remnawave** — FCP stores only the **panel** (`baseUrl` + `apiToken`), never individual nodes; per-node config reaches users via the panel's subscription output. Opt in with `fcp_register_remnawave_panel: true`.

**Node placement (per-mode squad pools).** FCP homes each new key into the chosen connection mode's squad pool, bound via `PATCH /api/v1/admin/backends/remnawave/mode-placements` (scope `admin:servers:write`; needs the FCP 2026-07-28 DB-driven mode-catalog release — older role versions used the still-alive `/api/v1/admin/remnawave/mode-placements` alias with the pre-rename ids `evade`/`privacy`). The default topology is **one panel-wide squad per transport**: bootstrap creates `FreeSocks-Fastly` (→ `freedom-ws`), `FreeSocks-Reality` (→ `privacy-reality`) and `FreeSocks-Relay` (→ `freedom-reality`) and binds them as the pools; every node activates the shared base inbounds and adds its own Hosts, so a subscription carries every node's endpoint. Retiring a node (`force_wipe_remnawave`) deletes its Hosts and node entry; squads and pools stay. Squad UUIDs are `no_log`'d — FCP validates them server-side and audits only a `poolBound` boolean + pool size. A **legacy per-node model** (`remnawave_per_node_placement: true`) clones inbounds per node with its own `FSF-`/`FSR-<hostname>` squad (panel caps names at 30 chars), appended to the pools and detached at teardown.

**Migrate cleans up the source row.** An Outline migration registers the destination under its own slug, then deletes the source's row by the source slug (`source_kv_hostname`) — a single idempotent `DELETE …/by-slug/{slug}`. Skipped when source and destination slugs match.

## Fastly fronting (CDN)

Fastly provides WebSocket passthrough in front of a node (Outline WSS and Remnawave-via-Caddy). Set `cdn_provider: fastly` on the domain and pick one client-edge scheme:

| Scheme | Vars | Client edge | Notes |
|---|---|---|---|
| Shared label | `fastly_edge_label: "cdn-content"` | `cdn-content.global.ssl.fastly.net` | Memorable; globally first-come |
| Shared random | `fastly_edge_random: true` | `fs-<hostname>.global.ssl.fastly.net` | Opt-in random |
| **Fronting domains** | `fastly_fronting_domains: [...]` / `fastly_fronting_zones: [...]` | your own domains | **Recommended** — `global.ssl.fastly.net` is widely blocked |
| Legacy custom | `fastly_domain_mode: custom` | `<hostname>.<base_domain>` | Same-domain edge |

**Fronting edges** — two complementary lists whose union is the effective edge set (first entry = primary):

```yaml
fastly_fronting_domains: ["cdn-content.example"]                    # exact, stable, operator-chosen
fastly_fronting_zones: ["video-streams.example", "quick-cdn.test"]  # zones for RANDOM per-node subdomains
```

- **Generated edges** (`fastly_fronting_zones`, recommended) mint unique, unguessable 2-word subdomains per node. They are **ephemeral** — rotation generates fresh labels and retires the old CNAME/TLS-subscription/ACME record automatically. Many nodes can share a zone.
- **Exact edges** (`fastly_fronting_domains`) are stable; their CNAMEs are re-pointed on rotation, never deleted.
- Each edge gets its **own Fastly TLS subscription** (Let's Encrypt ACME dns-01) and a CNAME in **its own DNS zone** (exact: longest-suffix match; generated: the zone itself). Every domain/zone must be covered in `domain_providers` with a `zone_id` — the role fails fast on any uncovered entry.
- Remnawave **Hosts are created per transport × edge** for client failover; with more than one edge a deterministic 6-hex suffix is added to the remark.
- CNAME writes are idempotent and non-destructive; your A/AAAA records on a fronting name are never auto-deleted.
- Edges are set at deploy time — adding one to a live node means rotating. N edges = N TLS subscriptions (possible Fastly cost).

**Hidden origin.** The node's only public A record is the unguessable `<hostname>-<rand8>.<base_domain>`:

- **shared + fronting modes**: `dns_hostname` **is** the suffixed origin (Caddy cert, Fastly SNI, panel address, FCP apiUrl all use it), so zone enumeration can't map a hostname to an origin IP;
- **legacy custom**: the clean hostname CNAMEs to the edge; the origin record is written separately;
- **Reality (no CDN)**: the clean hostname, no suffix — its address is public by design.

The suffix is persisted and regenerated on rotation (old origin record deleted). It defeats zone enumeration / passive DNS, not public CT logs. Panel caps are respected throughout: node names ≤ 30, squad names ≤ 30, Host remarks ≤ 40.

## Remnawave node

Deploys a `remnawave/node` Xray-core container managed by an external panel; all proxy/user config is pushed by the panel at runtime.

```yaml
remnawave_enabled: true
outline_enabled: false                      # mutually exclusive

remnawave_node_image: "remnawave/node:3.4.1"   # keep on the panel's major line
remnawave_node_nftables_logging: false       # node 3.x: no nftables kernel log lines (client IPs)
remnawave_node_port: 2222                    # Panel ↔ Node API port
remnawave_node_install_dir: "/opt/remnanode"
remnawave_log_dir: "/var/log/remnanode"

remnawave_panel_url: "https://panel.example.com"

# SECRET_KEY source: "vault" (default) or "panel_api" (GET {panel_url}/api/keygen)
remnawave_secret_key_source: "vault"
remnawave_panel_secret_key: "<vault base64 payload>"
remnawave_panel_api_token: "<vault admin token>"   # needed for panel_api or register_node

remnawave_panel_register_node: false         # POST /api/nodes (opt-in)
remnawave_panel_config_profile_uuid: "<config-profile-uuid>"
remnawave_panel_country_code: "US"

# Standalone Caddy (TLS + WS reverse-proxy) — required for Fastly fronting / VLESS-TLS.
# The Reality and relay transports don't need this (REALITY terminates in Xray).
remnawave_caddy_enabled: false
remnawave_caddy_email: "admin@example.com"
remnawave_caddy_listen_port: 443
# DEPRECATED alias, kept so stale references resolve; equals the `ws` internal_port.
remnawave_caddy_xray_internal_port: 8443

remnawave_decoy_root: "/var/www/decoy"       # camouflage site on non-transport paths
```

> `SECRET_KEY` is **panel-wide**, not per-node — the same value is used by every node on a given panel. It bundles a JWT public key + ECC cert payload (base64).

### Two workflows

**Panel UI (default, simplest):** create the node in the Panel UI (`Nodes → Management → +`), copy the generated `SECRET_KEY` into vault as `remnawave_panel_secret_key`, run the role with `remnawave_enabled=true`, then finish the panel flow by selecting a Config Profile.

**Panel API (opt-in, fully automated):** create a long-lived token via Panel UI → Tokens (`remnawave_panel_api_token`), set `remnawave_secret_key_source: panel_api` and/or `remnawave_panel_register_node: true`, and supply `remnawave_panel_config_profile_uuid` (from the bootstrap output). Active inbounds are derived from the enabled transports. The role calls `POST /api/nodes`, captures the returned UUID, and persists it to `/opt/remnanode/.node_uuid` for later change/migrate flows (which `PATCH /api/nodes` to keep the panel in sync).

### Panel-side node address

The panel needs an address to reach each node's management API on `remnawave_node_port` (default 2222). Two ways to spell it, and the interface form is the reliable one on an overlay network:

```yaml
# Preferred: register the IP this node carries on an interface.
remnawave_node_address_interface: "tailscale0"   # empty = use the name below
remnawave_node_address_family: "ipv4"            # ipv4 | ipv6 (global scope, unbracketed)

# Fallback: a name. Defaults to the node's origin FQDN (the unguessable
# fastly_origin_fqdn when Fastly-fronted, else dns_hostname).
remnawave_node_address: "node-a1b2c3d4.example.com"

remnawave_node_reconcile_address: false   # PATCH a reused node whose address drifted
```

Prefer the interface when the panel reaches nodes over Tailscale or WireGuard. The panel makes its node API calls from **inside a Docker container**, which resolves Tailscale MagicDNS names unreliably at best — so a MagicDNS name here is a node that goes unmanageable for reasons that look like nothing. Deploy, change and migrate all resolve it the same way (`tasks/providers/remnawave/resolve_node_address.yml`), from facts gathered on the node itself, so nothing has to be restated per host.

Resolution fails loudly rather than registering a blank or a guess: a missing interface lists the ones that do exist, and an interface with no address in the requested family (or only a link-local IPv6) is an error. Facts are re-gathered on the fly when the interface isn't in the current fact set, so `gather_facts: false` and "Tailscale came up earlier in this run" both work.

This is the **management** endpoint only. The data plane — Fastly origin, Reality address, client-facing Hosts — is unaffected.

**Moving an already-registered fleet.** Registration is GET-first: a node already on the panel under this name is reused and its address left alone. Switching a live fleet from hostnames to overlay IPs therefore needs `remnawave_node_reconcile_address: true`. Without it the drift is still logged every run (`Panel node "x" is registered as … but this run resolves …`), so nothing rots silently; a `change`/`migrate` run also PATCHes the address as part of its normal flow.

For a node already deployed (where `deploy` refuses to re-run), the transition is **`update` mode** with the same flag — the only lifecycle mode that touches a live node without rotating it. It needs the panel token (from vault) and the node's `.node_uuid` on disk; both are reported if missing, and neither is the deploy-time `remnawave_panel_register_node` flag:

```bash
ansible-playbook -i inventory playbook.yml --ask-vault-pass --extra-vars "target=node-a operation_mode=update environment_mode=prod outline_enabled=false remnawave_enabled=true remnawave_panel_url=https://panel.example.com remnawave_node_address_interface=tailscale0 remnawave_node_reconcile_address=true"
```

### Transports

Three transports, chosen by network environment:

| Transport | Enable | Strength | Best for |
|-----------|--------|----------|----------|
| **VLESS+WS+TLS** (via Caddy, real LE cert) | `remnawave_caddy_enabled` + a `ws` transport | Rides ordinary HTTPS — traverses forced proxies; TLS inspection sees normal HTTPS | **Business / school** networks |
| **VLESS+Vision+REALITY** (direct) | `remnawave_reality_enabled` | Fastest; best against active probing (raw TCP, mimics a real site's TLS) | **Open** networks |
| **VLESS+REALITY via an L4 relay** | `remnawave_relay_enabled` | REALITY's probe-resistance **plus** a hidden node IP; the edge is a shared address whose blocking carries collateral damage | **Heavily censored** networks |

In one line: **WS+TLS** gets through forced HTTP proxies and TLS inspection because it looks like and rides normal HTTPS. **Reality** is fastest and strongest against active probing but does *not* traverse forced proxies. **Relay** trades one extra hop of latency for hiding the node IP entirely, which is what matters once a censor blocks endpoints by address.

#### CDN transports (WebSocket)

CDN-fronted transports are a **data-driven list** — each entry maps 1:1 to one panel inbound, one Caddy route, one Fastly VCL rule, and one Remnawave Host, so the layers can't drift.

```yaml
remnawave_cdn_transports:
  - name: ws
    network: ws
    path: "/ws"            # PANEL-GLOBAL — must equal the inbound's wsSettings.path
    internal_port: 8443    # must equal the inbound's listen port
    inbound_uuid: ""       # configProfileInboundUuid
    alpn: "http/1.1"
    fingerprint: chrome
    enabled: true
```

- **Path is panel-global, not per-node-random** (opposite of Outline WSS). Every node on that inbound uses the same path.
- **Caddy + decoy:** Caddy path-routes each `ws` transport to its loopback inbound and serves a plausible "maintenance" page on all other paths, so a probe of `https://<host>/` sees an innocuous site. The panel-side inbound must bind the internal port (e.g. `127.0.0.1:8443`) with **plaintext WebSocket** — Caddy terminates TLS, not Xray.
- **Automatic Host creation:** with node registration on, the role creates one Host per enabled transport (`POST /api/hosts`, idempotent by `remark`). Hosts are what put a node's keys into subscriptions — without them a registered node never appears in subscription output. Address/SNI is the Fastly edge when fronted, else the origin `dns_hostname` (a direct Caddy node is a complete endpoint, e.g. `wss://<dns_hostname>/ws`); port 443 with `securityLayer: TLS`. Gated on `remnawave_enabled` + `remnawave_panel_register_node` + `remnawave_caddy_enabled` + a `cdn_provider` of `fastly` or `none`. Rotation/migration deletes the old node's Hosts first.

**Phase 0 (WS)** — one-time manual prerequisite (done for you by `operation_mode=bootstrap`): add a raw-Xray inbound to the Config Profile — tag `VLESS_WS_CDN`, `listen 127.0.0.1`, `port 8443`, `network ws`, `security none`, `wsSettings.path /ws` — and record its UUID (from `GET /api/config-profiles/inbounds`) into `remnawave_cdn_transports[].inbound_uuid`.

> **Why WS only (no XHTTP):** Fastly cannot relay XHTTP's long-lived streamed download — its `do_stream` is a cache feature that explicitly forbids endless responses, and Fastly staff confirmed the equivalent long-lived gRPC stream is impossible via VCL (only via Fastly Compute). Cloudflare fronting is off the table (VPN fronting is banned there). WS-over-Fastly is the shape Fastly supports.

#### Reality transport (direct, no Caddy/CDN)

Reality is a **direct** mode: Xray terminates TLS itself by borrowing a real site's handshake, binding `:443` directly.

```yaml
remnawave_enabled: true
outline_enabled: false
remnawave_caddy_enabled: false           # MUST stay off — Reality binds :443 itself
remnawave_reality_enabled: true
remnawave_reality_inbound_uuid: "<configProfileInboundUuid of the Reality inbound>"
remnawave_reality_sni: "<a serverName from the inbound>"
# remnawave_reality_address: ""          # client-facing address; defaults to dns_hostname
# remnawave_reality_port: 443
# remnawave_reality_fingerprint: chrome
remnawave_panel_register_node: true
remnawave_panel_config_profile_uuid: "<config-profile-uuid>"
```

- **Constraints (asserted):** mutually exclusive with `remnawave_caddy_enabled` (both bind `:443`) and incompatible with Fastly; the role forces `dns_proxied: false`.
- **Keys live on the panel,** not the role: `publicKey`/`shortIds`/`privateKey`/`flow` are on the panel inbound. The role only creates the Host (remark `<hostname>-reality`, `securityLayer: DEFAULT`; the panel derives `pbk`/`sid`/`flow`). The Reality inbound UUID joins the derived active-inbound set when enabled.
- **Phase 0 (Reality):** add a Reality inbound (raw Xray: `vless`, `listen 0.0.0.0:443`, `security reality`, `realitySettings {dest, serverNames, privateKey, shortIds}`, `flow xtls-rprx-vision`), generate the x25519 keypair, and record the inbound UUID + one serverName into `remnawave_reality_inbound_uuid` / `remnawave_reality_sni`.

#### Relay transport (VLESS+REALITY behind an external L4 proxy)

The heaviest-censorship transport. The client dials an **external L4/TCP proxy edge you create yourself**; the proxy forwards **raw TCP** to the node, where REALITY terminates. An L4 forwarder is transparent to REALITY, so there is **no TLS at the edge** — no cert, no ACME, no Caddy, no decoy web root — and **the node's address never appears in a client config**.

Versus direct Reality, relay adds two things: the node IP is hidden behind the edge (so IP-blocking the node isn't available to a censor), and the decoy can resolve into the *same network as the edge the client is talking to*, removing the `(IP, SNI)` mismatch a censor can otherwise score.

```yaml
remnawave_enabled: true
outline_enabled: false

# A relay node is DEDICATED — the role asserts all of these.
remnawave_caddy_enabled: false            # MUST stay off (contends for :443)
remnawave_reality_enabled: false          # MUST stay off (contends for :443)
remnawave_per_node_placement: false       # legacy path does not know relay
# remnawave_cdn_transports: []            # not needed: the role drops inherited
                                          # CDN transports on a relay node itself

remnawave_relay_enabled: true
remnawave_relay_inbound_uuid: "<configProfileInboundUuid of the relay inbound>"
remnawave_relay_address: "<the EXTERNAL proxy edge — FQDN or IP>"
remnawave_relay_sni: "<a serverName from the relay inbound>"
# remnawave_relay_port: ""                # client-facing port ON THE EDGE (empty = 443)
# remnawave_relay_fingerprint: ""         # empty = chrome
remnawave_relay_repoint_ack: false        # required before change/migrate breaks the proxy origin

remnawave_panel_register_node: true
```

**Two ports, and conflating them is the most common mistake.** `remnawave_relay_port` is the port **on the edge** that clients dial (it lands in the `vless://` link). `remnawave_bootstrap_relay_listen_port` is the port the inbound binds **on the node** — that is what you point the proxy's *backend* at. One edge can front several nodes by mapping a different edge port to each, so the former is per-node. Both bootstrap and the deploy summary print the exact backend target.

> Bootstrap echoes the node-side port into its output file as `remnawave_relay_node_listen_port` (alongside `remnawave_relay_inbound_uuid` and `remnawave_relay_sni`, taken from the first `serverNames` entry) purely so node deploys can print that line — it configures nothing. Because that file loads as extra-vars, **do not also set `remnawave_relay_sni` in your own vars file**: the later `-e` would win and the effective value would depend on argument order.

**`remnawave_relay_address` has no default, on purpose.** It is asserted, never defaulted to `dns_hostname`: a fallback would mean any run that forgot the variable silently publishes the node's real hostname to every subscriber and bypasses the proxy — worse than a failed run.

**The edge is checked against the node, not just its hostname.** One file, `tasks/providers/remnawave/assert_relay_edge.yml`, owns the invariant. Deploy, `change` and `migrate` each include it as an early pre-flight so a bad edge fails before anything is mutated, and `create_relay_host.yml` includes it again — which makes it unavoidable, since every path that publishes a relay Host goes through there. It refuses an edge that is, or resolves to, this node: `dns_hostname`, **both** FQDNs of a rotation, `default_ipv4`/`default_ipv6`, every entry of `all_ipv4_addresses`/`all_ipv6_addresses`, this host's `envoy_mappings` addresses, and a best-effort `getent` resolution compared against all of the above. The resolution pass fails **only on a positive match** — an edge you can't resolve from the control host isn't an error, since your resolver isn't the client's. Documented limit: equivalent IPv6 *spellings* are not canonicalized (`2001:db8::1` vs `2001:0db8:0:0:0:0:0:1` compare as different strings), so give the edge in the form the node's facts report; `tests/test_relay.yml` `(t3)` pins that gap so it can't regress unnoticed.

**PROXY protocol.** Many L4 forwarders prepend a PROXY v1/v2 header. If yours does, set `remnawave_bootstrap_relay_accept_proxy_protocol: true` — otherwise REALITY reads the header bytes as a malformed ClientHello and **every connection fails with no useful error**. If yours does not, leave it off; the same failure happens in reverse.

**What the role does not do.** It never talks to a proxy provider: you create the proxy and point its backend at the node by hand. On rotation and migration the role tells you exactly what to re-point, and requires `remnawave_relay_repoint_ack=true` before doing anything that would irrecoverably break an FQDN-based proxy origin. An acknowledged rotation additionally requires `custom_hostname`, so the name the proxy must point at is decided by you rather than regenerated.

**The node remembers it is a relay node.** Deploy writes `address=`/`port=`/`fingerprint=` to `.relay_address` in the install dir, and **the existence of that file is the authoritative marker**. `change`, `migrate` and `update` read it, so they keep working without re-passing the relay flags — and `migrate` uses it to tell a relay node from a direct-Reality one (neither has a Caddyfile, so nothing else can). It also means the endpoint survives a lifecycle run that forgets `remnawave_relay_port`/`_fingerprint` instead of silently resetting to defaults and re-creating the Host every run.

**FCP mode.** The relay squad binds to FCP's `freedom-reality` pool (direct Reality keeps `privacy-reality`). `freedom-reality` **ships disabled** in FCP's catalog and the role cannot enable it (that needs `admin:settings:write`, which the role's token deliberately does not hold) — enable it in **FCP Admin → Connection modes** once at least one relay node exists.

#### Decoys and bootstrap-side inbound variables

**Decoy sites are operator-supplied.** The role ships **no default** for `remnawave_bootstrap_reality_dest` / `_server_names` or their relay counterparts: which site a REALITY inbound borrows is deployment-specific and does not belong in a public repo. Set them in your own inventory — bootstrap asserts they are non-empty rather than creating a dead inbound. The mechanical requirements: `dest` must be a real, reachable TLS 1.3 host that serves **every** name in `serverNames` (a probe sending an unserved name gets a cert mismatch, which burns the node), its certificate chain must fit Xray's hardcoded 8192-byte REALITY buffer ([xray-core #6356](https://github.com/XTLS/Xray-core/issues/6356)), and `remnawave_reality_sni` must be one of those `serverNames`.

These shape the inbounds bootstrap creates; they are read during bootstrap only, never on a node deploy. Each REALITY transport carries its own set — and its own x25519 keypair, so a seized direct-Reality node does not burn relay:

```yaml
# Direct Reality
remnawave_bootstrap_reality: true                  # false to skip the inbound entirely
remnawave_bootstrap_reality_dest: ""               # REQUIRED — "<decoy host>:443"
remnawave_bootstrap_reality_server_names: []       # REQUIRED — names that dest serves
remnawave_bootstrap_reality_short_ids: [""]
remnawave_bootstrap_reality_network: "raw"
remnawave_bootstrap_reality_squad_name: "FreeSocks-Reality"

# Relay (defaults ON — set false if you aren't deploying relay nodes yet,
# otherwise bootstrap asserts on the empty dest/serverNames below)
remnawave_bootstrap_relay: true
remnawave_bootstrap_relay_dest: ""                 # REQUIRED when relay is on
remnawave_bootstrap_relay_server_names: []         # REQUIRED when relay is on
remnawave_bootstrap_relay_short_ids: [""]
remnawave_bootstrap_relay_network: "raw"
remnawave_bootstrap_relay_listen_port: 443         # port the inbound binds ON THE NODE
remnawave_bootstrap_relay_accept_proxy_protocol: false
remnawave_bootstrap_relay_squad_name: "FreeSocks-Relay"

# Re-running bootstrap adds base inbounds missing from an existing profile, by tag
remnawave_bootstrap_reconcile_inbounds: true
```

**Adding relay to a panel that is already bootstrapped.** Reconciliation is **by tag**: re-running bootstrap against an existing `FreeSocks-Config` adds any missing base inbound via a full-replace `PATCH /api/config-profiles`. Existing inbounds are copied through **byte-for-byte and never rewritten**, so a re-run cannot rotate a live inbound's keys out from under connected clients. The corollary: editing `dest`/`serverNames` on an inbound that already exists is *not* reconciled — change it in the panel, or delete the inbound and re-run.

### Caddy + Xray port coordination

Xray runs in `network_mode: host`, so it shares host ports. When Fastly fronts the node, its origin needs a real public TLS cert at the node — hence **standalone Caddy on the host** (not embedded like Outline's). Caddy listens on `remnawave_caddy_listen_port` (default 443), auto-issues via HTTP-01 (port 80 must be free at provisioning and renewal), and path-routes each enabled transport to `127.0.0.1:<internal_port>`, with all other paths falling through to the decoy. The panel-side inbound must match: bind the internal port with **plaintext WebSocket** — TLS termination is Caddy's job, not Xray's.

If Caddy is disabled, use Reality or relay (no public cert needed) or mount your own cert files into the container at `/var/lib/remnawave/configs/xray/ssl/`.

### Notes

- **slipstream coexistence:** `slipstream_mode=raw` (microsocks-backed) works alongside Remnawave; `slipstream_mode=shadowsocks` requires Outline.
- **FCP registers the panel, not the node** — see the FCP section above.

## Outline: WebSocket (WSS) support

`outline_wss_enabled: true` makes Caddy handle HTTPS on 443 and proxy WebSocket traffic to Shadowsocks, so it looks like regular web traffic.

```yaml
outline_wss_enabled: true
outline_caddy_auto_https: true
outline_caddy_email: "admin@example.com"
outline_caddy_domain: ""              # defaults to server hostname

outline_wss_random_paths: true        # false to use custom paths below
outline_wss_random_path_min_words: 3
outline_wss_random_path_max_words: 5
outline_wss_tcp_path: "/tcp"
outline_wss_udp_path: "/udp"
outline_wss_server_port: 8080         # internal, not exposed

outline_api_proxy_path: "/api"        # valid-TLS API at https://domain/api/... (for FCP)
api_hostname_suffix: ""               # "" = same domain; "-api"/"-prom" = legacy subdomains
prom_hostname_suffix: ""
```

> When using WSS, set `outline_keys_port` off 443 (e.g. 853) so Caddy can use 443.

> **FCP forces `/tcp` + `/udp`:** FCP issues Outline WSS keys with fixed client paths and no per-server path field. When both `fcp_enabled` and `outline_wss_enabled` are true, the role overrides the WSS paths to `/tcp` + `/udp` and disables randomization (logged) — otherwise issued keys would point at random paths Caddy isn't serving. Path randomization only applies on a non-FCP Outline deploy.

## Outline: slipstream DNS tunnel

`slipstream_enabled: true` tunnels traffic through DNS queries via recursive resolvers for extreme censorship resistance. slipstream **builds from source** (Rust on the target; can take several minutes).

```yaml
slipstream_enabled: true
slipstream_mode: "shadowsocks"        # or "raw"
slipstream_resolver: "77.88.8.8:53"   # Yandex DNS (on Russia's allowlist)
slipstream_resolver_backup: "77.88.8.1:53"
slipstream_version: "main"
slipstream_dns_port: 53
slipstream_socks_port: 1080           # raw mode

# Required (via --extra-vars):
slipstream_base_domain: "your-dns.example"  # must be in domain_providers
slipstream_subdomain: "dns1"                # tunnel subdomain
slipstream_ns_hostname: "ns1"               # nameserver hostname
slipstream_create_dns_records: true         # auto-create NS + A/AAAA (default true)
```

| | `shadowsocks` mode | `raw` mode |
|---|---|---|
| Server target | outline-ss-server:443 | microsocks (SOCKS5):1080 |
| Client needs | slipstream-client + ss-local | slipstream-client only |
| Encryption | QUIC + Shadowsocks | QUIC only |
| Coexists with | Outline | Outline **or** Remnawave |

When `slipstream_create_dns_records: true`, the role delegates the tunnel subdomain to the server (`dns1 IN NS ns1`, plus `ns1` A/AAAA). Use different `slipstream_subdomain`/`slipstream_ns_hostname` per server (`dns1`/`ns1`, `dns2`/`ns2`, …); infrastructure-looking names (`dns`, `mail`, `ns`, `api`, `cdn`) draw less attention. Client build/usage is documented in the slipstream-rust repo.

> slipstream artifacts (cert, resolver config) are generated on the server; they are **not** published to FCP today. Delivering them to clients is out of scope for control-plane registration.

## Component flags reference

| Flag | Default | Description |
|------|---------|-------------|
| `outline_enabled` | `true` | Outline Shadowsocks server (excludes `remnawave_enabled`) |
| `outline_wss_enabled` | `false` | WebSocket transport (needs Outline; `outline_keys_port != 443`) |
| `remnawave_enabled` | `false` | Remnawave Xray node (excludes `outline_enabled`) |
| `remnawave_panel_register_node` | `false` | Auto-register node via `POST /api/nodes` |
| `remnawave_caddy_enabled` | `false` | Standalone Caddy (TLS + WS proxy) — required for Fastly fronting |
| `remnawave_reality_enabled` | `false` | VLESS+Vision+Reality direct node (needs `_inbound_uuid` + `_sni`) |
| `remnawave_relay_enabled` | `false` | VLESS+REALITY behind an external L4 proxy — DEDICATED node (asserts Caddy / direct Reality / Fastly / per-node placement all off; needs `_inbound_uuid` + `_address` + `_sni`) |
| `remnawave_relay_repoint_ack` | `false` | Acknowledge you'll re-point the proxy backend before a relay `change`/`migrate` breaks it |
| `remnawave_node_reconcile_address` | `false` | PATCH a reused panel node whose address/port drifted |
| `remnawave_per_node_placement` | `false` | Legacy per-node inbound clones + squads (default = shared squads) |
| `force_reinstall_remnawave` | `false` | Re-template compose + recreate the node container |
| `force_wipe_remnawave` | `false` | **Destructive**: tear down placement, delete panel node + install dir |
| `fcp_enabled` | `false` | Register server (Outline) / panel (Remnawave, opt-in) with FCP |
| `slipstream_enabled` | `false` | Deploy slipstream DNS tunnel |
| `slipstream_mode` | `shadowsocks` | `shadowsocks` (tunnel to SS) or `raw` (direct SOCKS5) |
| `force_reinstall_slipstream` | `false` | Reinstall slipstream even if present |
| `force_rebuild_slipstream` | `false` | Rebuild the slipstream binary from source |
| `force_reinstall_wss` | `false` | Regenerate WSS config |

## Mode examples

```bash
# Deploy: Outline only (default backend)
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=deploy environment_mode=prod deploy_target_domain=example.com"

# Deploy: Outline + WSS (CDN fronting)
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=deploy environment_mode=prod deploy_target_domain=example.com outline_wss_enabled=true"

# Deploy: slipstream only (raw mode, no Outline)
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=deploy environment_mode=prod deploy_target_domain=example.com" \
  --extra-vars "outline_enabled=false slipstream_enabled=true slipstream_mode=raw slipstream_base_domain=your-dns.example"

# Deploy: Remnawave node (Panel API workflow — role fetches SECRET_KEY + registers node)
ansible-playbook playbook.yml --ask-vault-pass \
  --extra-vars "operation_mode=deploy environment_mode=prod deploy_target_domain=example.com" \
  --extra-vars "outline_enabled=false remnawave_enabled=true remnawave_panel_url=https://panel.example.com" \
  --extra-vars "remnawave_secret_key_source=panel_api remnawave_panel_register_node=true" \
  --extra-vars "remnawave_panel_config_profile_uuid=<uuid> remnawave_panel_country_code=US"

# Change: rotate hostname (optionally to a different domain)
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=change environment_mode=prod change_target_domain=example.app"
# add change_delete_old_dns=false to keep old records
# a relay node also needs remnawave_relay_repoint_ack=true and custom_hostname=<name>

# Migrate: move a server to a new host (backend + transport auto-detected)
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=migrate environment_mode=prod dns_provider=cloudflare" \
  --extra-vars "source_hostname=old-server source_kv_hostname=apple-banana" \
  --extra-vars "destination_hostname=new-server destination_kv_hostname=apple-banana"

# Update: add slipstream (raw) to an existing Outline server
ansible-playbook playbook.yml \
  --extra-vars "operation_mode=update environment_mode=prod" \
  --extra-vars "slipstream_enabled=true slipstream_mode=raw slipstream_base_domain=your-dns.example" \
  --extra-vars "slipstream_subdomain=dns1 slipstream_ns_hostname=ns1"

# Update: re-pull and recreate a Remnawave node (new image tag)
ansible-playbook playbook.yml --ask-vault-pass \
  --extra-vars "operation_mode=update environment_mode=prod" \
  --extra-vars "outline_enabled=false remnawave_enabled=true remnawave_panel_url=https://panel.example.com" \
  --extra-vars "remnawave_node_image=remnawave/node:3.4.1 force_reinstall_remnawave=true"
```

**What each mode does:**

- **deploy** — validate → generate hostname → create DNS (before install, so the Outline installer's self-check can't negative-cache the missing name) → install base + enabled components → register with FCP.
- **change** — read existing config → new hostname on the target domain → create DNS → update hostname, then Caddy (that order matters: the reverse breaks API access) → restart + health check → re-sync FCP → optionally delete old DNS.
- **migrate** — verify source + detect backend *and transport* (the `.relay_address` marker distinguishes relay from direct Reality; neither has a Caddyfile) → verify destination is clean → install matching backend → copy config → update DNS → re-point FCP (and delete the stale source row for Outline).
- **update** — detect existing install + components → cross-check requested vs detected backend → add requested components (idempotent; `force_reinstall_*` to redo) → re-sync FCP.

## Directory structure

```
tasks/
├── main.yml                    # Orchestrator + provider routing
├── setup/                      # install, outline, outline_api_proxy, websocket,
│                               #   wss_paths, docker, remnawave, caddy, decoy, slipstream
├── change/                     # change (dispatcher), outline_change, remnawave_change
├── migrate/                    # migrate, transfer_config, transfer_remnawave, containers
├── update/                     # remnawave_update
└── providers/
    ├── cloudflare/             # dns, slipstream_dns, migrate/
    ├── fastly/                 # origin_identity, service, tls, tls_domain, dns, dns_edge, cleanup
    ├── remnawave/              # keygen, resolve_node_address, register_node, update_node,
    │                           #   delete_node, create_hosts, create_reality_host,
    │                           #   create_relay_host, assert_relay_edge, cleanup_hosts,
    │                           #   gen_x25519, create_config_profile, create_squad,
    │                           #   create_node_placement, teardown_node_placement
    └── fcp/                    # register_server, register_remnawave_panel, delete_server,
                                #   bind_placements, bind_default_mode, status_gate
```

## Testing

Every push and PR runs three CI jobs (`.github/workflows/ci.yml`): **lint** (YAML + syntax), **unit** (offline expression tests), and **integration** (end-to-end against a real Remnawave panel).

**Unit tests** (offline, `connection: local`, no hosts/APIs):

```bash
tests/run.sh                              # every tests/test_*.yml
ansible-playbook tests/test_bootstrap.yml # or one at a time
```

| Test | Covers |
|---|---|
| `test_bootstrap.yml` | the three base inbounds + tag→UUID mapping, FCP mode-placement bodies (full-replace + per-node `addSquadUuids`), the legacy per-node inbound plan/clone logic incl. re-run idempotency, x25519 shape tolerance |
| `test_relay.yml` | the relay transport and the lifecycle gates it shares with the other direct transports: `.relay_address` parsing/precedence, the `cleanup_hosts.yml` remark regex, the migrate source-capability truth table, change/migrate repoint gates, relay-only inbound activation, Host drift, the anti-leak invariant, plus **structural** guards pinning task ORDERING (destination gates before the first mutation, Host GET before the idempotency decision, gates keyed on resolved `*_effective` facts rather than raw flags) |
| `test_node_address.yml` | interface-vs-name precedence, IPv6 global-scope selection, sanitized fact keys, both platform fact shapes, and every fail-closed path |
| `test_caddyfile_render.yml` | the Remnawave Caddyfile structure (transport routes + decoy fallback) |
| `test_fcp_and_hosts.yml` | FCP Outline `apiUrl` construction + Remnawave Host body shaping |
| `test_wss_paths.yml` | WSS path precedence (configured vs FCP-forced `/tcp` + `/udp`) |
| `test_fastly_edge.yml` | Fastly edge-name selection + the service-named-after-hostname rule |
| `test_origin_identity.yml` | origin suffix shape, `dns_hostname` reshaping per mode, fronting-zone resolution, mutual-exclusion failures, multi-edge remarks + cleanup regex |

**Integration harness** (`tests/run_integration.sh`) stands up an ephemeral Remnawave panel (pinned release, fresh DB per run), a contract-strict mock FCP (`tests/mock_fcp.py` — validates requests exactly like the real control plane, including Convex-style undeclared-field rejection and squad-UUID checks), and a mock Cloudflare API, then runs:

1. **`tests/test_integration.yml`** — task-level: bootstrap (no-log posture, all three base inbounds, three shared squads created and bound to the FCP pools), two shared-model node registrations (no clones, no per-node squads, no pool appends), a **relay node** (it inherits the WS transport from the bootstrap output yet must activate only its relay inbound; its Host must advertise the edge, never the node; endpoint drift re-points the Host; the `.relay_address` marker round-trips), a shared-model retire (Host cleanup by remark — one node's Hosts only — then node DELETE) and a relay retire, plus the legacy per-node placement path for two simulated nodes (idempotent re-runs, the duplicate inbound-port case, append-only pools, teardown).
2. **`tests/test_deploy.yml`** (`RUN_DEPLOY_PHASE=1`) — the real `operation_mode=deploy` path: validation, hostname, mock DNS, apt installs, a real `remnawave/node` container the panel actually connects to, the Reality Host, and FCP registration + bound pools + the status gate. Reality is used because it's the one production topology needing no public TLS issuance.

```bash
bash tests/run_integration.sh                     # task-level phases (any OS)
RUN_DEPLOY_PHASE=1 bash tests/run_integration.sh  # + real deploy (Linux + passwordless sudo)
```

Requires Docker, ansible-core ≥ 2.15, python3. Both mocks are ephemeral and everything is torn down on exit (including `/opt/remnanode` from the deploy phase); CI always runs the deploy phase. A syntax check of the example is also useful:

```bash
ansible-playbook --syntax-check example-playbook.yml
```

## License

GNU General Public License v3.0

## Author Information

Maintained by the [Unredacted](https://unredacted.org/) Team.
