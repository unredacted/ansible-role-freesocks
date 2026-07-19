# Plan: hardening fixes + hostname identity + Fastly origin randomization + renames

> Working document. Phases are ordered so each lands green (lint + unit + integration)
> before the next starts. P0/P1 = bug fixes from the 2026-07-18 codebase review;
> P2–P4 = requested feature changes; P5 = FCP verification (conclusion: no FCP changes).
>
> **2026-07-18 status: P0 ✅, P1 ✅ (+ pivot), P2 ✅, P3 ✅, P4 ✅, P5 ✅ — ALL DONE.**
> **P4 as built**: CLAUDE.md (bootstrap/update/FCP-scopes/provider-tree/Fastly
> schemes/testing/version-history 3.1.0), README.md (bootstrap + squad-model
> prose, new "Fastly fronting (CDN)" section with the naming-model table,
> Caddy path-routing, deploy/migrate step order, directory structure, full
> test list, cross-ref fix), defaults/main.yml header (bootstrap mode).
> **P2 as built**: `tasks/providers/fastly/origin_identity.yml` derives the
> per-node origin (`<hostname>-<rand8>.<base_domain>`) in deploy/change/migrate;
> shared + fronting modes reshape `dns_hostname` to it; `fastly_fronting_domains`
> (list) implies the custom flow with per-domain TLS subscriptions
> (`tls_domain.yml` engine) + per-zone edge CNAMEs (`dns_edge.yml` engine);
> Fastly service = hostname, backend = origin name; Hosts per transport ×
> domain (6-hex remark suffix when >1 domain); `.node_name` persists the CLEAN
> hostname (since `.hostname` may now hold the suffixed origin); rotation
> deletes the old origin record via the persisted `fastly_origin_fqdn`;
> same-name rotation is re-keyed to hostname equality; `remnawave_node_address`
> defaults to the origin FQDN (fixes custom-mode panel reachability).
> **PIVOT (squad model)**: per the panel's 30-char squad-name cap, per-node
> squads with a `FreeSocks-Fastly-<hostname>` suffix are impossible — and the
> user doesn't want suffixed squads anyway. So the SHARED model is now the
> default (done in the P1 batch): bootstrap creates + binds panel-wide
> `FreeSocks-Fastly` / `FreeSocks-Reality` squads (profile renamed to
> `FreeSocks-Config`); node deploys activate the shared base inbounds (no
> clones/squads/appends); wipe now removes the node's Hosts (they bind the
> shared inbounds). Per-node placement is legacy opt-in
> (`remnawave_per_node_placement: false`) with short `FSF-`/`FSR-` prefixes +
> a hostname-length assert. P1-8c (squad rename on rotation) is MOOT on the
> default path (no per-node squads to rename). Item 17's derived-prefix idea
> is superseded (static shared names).

## Naming model (target state, P2+)

Example: `custom_hostname: xray1-front-mci1-fs-ce`, `deploy_target_domain: freesocks.org`.

| Layer | Fastly shared | Fastly custom (legacy, same-domain) | Fastly fronting-domain (NEW) | Reality (no CDN) |
|---|---|---|---|---|
| Remnawave node name | `xray1-front-mci1-fs-ce` | same | same | same |
| Remnawave node address (panel→node :2222) | origin FQDN | origin FQDN | origin FQDN | `<hostname>.freesocks.org` |
| Origin DNS A/AAAA (hidden) | `<hostname>-<rand8>.freesocks.org` | same | same | clean name, no suffix |
| Client-facing edge | `<label>.global.ssl.fastly.net` | `<hostname>.freesocks.org` CNAME→edge | `[domain1.com, domain2.com, …]` each CNAME→edge | n/a |
| Caddy cert name | origin FQDN | clean hostname | origin FQDN | n/a |
| Fastly service name | `<hostname>` | same | same | n/a |
| Fastly backend name / address | `<hostname>-<rand8>` / origin FQDN | same | same | n/a |
| Backend SNI / override_host | origin FQDN (= dns_hostname) | clean dns_hostname | origin FQDN | n/a |
| Host remark / address | `<hostname>-ws` / edge domain | same | `<hostname>-ws-<domain>` per domain / that domain | `<hostname>-reality` / clean FQDN |
| Per-node squads | `FreeSocks-Fastly-<hostname>` → FCP `evade` pool | same | same | `FreeSocks-Reality-<hostname>` → `privacy` |
| FCP (Outline) name/slug | `<hostname>`; apiUrl on origin FQDN; websocketDomain = edge | same | same | n/a |

`<rand8>` = 8 lowercase alnum chars, generated per node at deploy/change/migrate.
The random suffix makes the origin A record unguessable so passive-DNS enumeration
of the zone cannot map hostnames → origin IPs. Reality keeps the clean name: its
address is public by design (it goes into client subscription links).

**Why the fronting-domain flavor exists**: `global.ssl.fastly.net` is blocked in
many places, and even a same-domain custom edge ties the edge name to the origin
zone. `fastly_fronting_domains` (per-play var, LIST) decouples them: clients only
ever see e.g. `video-streams.org`; the origin hides behind an unguessable
suffixed record in a completely different zone. Multiple fronting domains on ONE
service give client-side failover when a domain gets blocked. Each fronting
domain attaches to exactly one Fastly service (Fastly constraint) — treat them
as per-node; they follow the node through rotation (change/migrate re-point
their CNAMEs, never delete them). First list entry = PRIMARY edge, used where a
single value is required (FCP `websocketDomain`).

---

## P0 — Verified bugs (small, independent)

1. **SECRET_KEY log leak** — add `no_log: true` to the compose re-render
   (`tasks/update/remnawave_update.yml:170-177`), matching `tasks/setup/remnawave.yml:113`.
2. **Vault guard chained vars** (`tasks/main.yml:15-26`) — split into a `set_fact`
   (materialize `_undecrypted_secrets`) + a separate `fail` task; removes the
   ansible-core 2.19+ same-block chain (`_undecrypted_secrets` ← `_secret_values`).
3. **Bare truthiness** (`tasks/setup/websocket.yml:68,87,96-97`) —
   `outline_caddy_auto_https | bool` / `outline_wss_random_paths | bool`,
   matching `outline_api_proxy.yml`'s pattern.
4. **Missing become** (`tasks/setup/slipstream.yml:261-264`) — `become: true` on the
   `ss -ulnp` check so non-root runs still see root-owned `systemd-resolve`.

Verify: `tests/run.sh` + `ansible-playbook --syntax-check` on touched files.

## P1 — Medium issues

5. **`enabled` default consistency** — treat a missing `enabled` key as `true`
   everywhere: `create_node_placement.yml:126` (`t.enabled | default(false)` →
   `default(true)`), plus the `selectattr('enabled')` spots
   (`create_node_placement.yml:158`, `create_hosts.yml:94`, `caddy.yml:115`)
   → `selectattr('enabled', 'equalto', true) | ...` replaced with
   `selectattr('enabled', 'undefined') + selectattr('enabled')`-style union, or
   simpler: normalize the list once (`| combine({'enabled': true}, recursive)`)
   and keep downstream as-is.
6. **Squad-UUID `no_log` drift** — add `no_log: true`:
   `create_node_placement.yml:350-363` (UUID resolve), `create_squad.yml:111-114`,
   `teardown_node_placement.yml:21-35, 54-67` (slurp/parse/fallback resolve).
7. **Migrate parity detection** — on the source host, stat `/etc/caddy/Caddyfile`
   and `{{ remnawave_node_install_dir }}/.node_uuid` + reality presence; propagate
   via `hostvars` (same pattern as `migrate_component`, `tasks/migrate/migrate.yml:36-39`)
   and use as fallback when the caller did not pass the flags:
   `remnawave_caddy_enabled | default(_migrate_caddy_detected)` etc. in the
   Remnawave parity block (`migrate.yml:390-533`). Explicit flags still win.
8. **Warnings for silent skips** —
   a. `remnawave_change.yml:346-378`: warn when `change_delete_old_dns` is on but
      `old_base_domain not in domain_providers` (old records leak).
   b. `update/remnawave_update.yml:92-96`: warn that a wipe without
      `remnawave_panel_api_token` orphans the per-node squads + FCP pool entries.
   c. Squad-name staleness after change-mode rotation: attempt panel-side rename
      if the API supports it (check `PATCH /api/internal-squads/{uuid}` on the
      pinned CI panel); else print a warning that squads keep the old hostname.
9. **Unguarded vars / bool hygiene** —
   `main.yml:822` `slipstream_base_domain | default('')`;
   `main.yml:920` + `migrate.yml:302-303` add `| default('')` before `| first | trim`;
   `main.yml:865` wrap in `| bool`;
   `remnawave_change.yml:24` add trailing `| default('')` to `cf_token`;
   deploy/change validation: assert `dns_provider == 'cloudflare'` value (not just
   key presence) so a typo fails fast instead of silently skipping DNS;
   update mode: add the FCP pre-flight credential check that deploy has
   (`main.yml:353-361`) before installing anything.

Verify: `tests/run.sh`; integration run for 6/8b (teardown paths).

## P2 — Hostname identity + Fastly origin randomization

10. **Early origin-identity derivation (deploy)** — new block in `tasks/main.yml`
    right after hostname facts (~line 455), before the DNS include (line 491),
    gated on `effective_cdn_provider == 'fastly'`:
    - `fastly_origin_suffix`: `lookup('community.general.random_string', ...)` or
      `password '/dev/null chars=ascii_lowercase,digits length=8'` lookup
      (pick whatever the role's collection deps already allow; check
      `requirements.yml` / CI-installed collections).
    - `fastly_origin_name: "{{ random_hostname }}-{{ fastly_origin_suffix }}"`,
      `fastly_origin_fqdn: "{{ fastly_origin_name }}.{{ base_domain }}"`.
    - Shared mode AND fronting-domain mode (item 11): override `dns_hostname` →
      `fastly_origin_fqdn` (single suffixed A record; Caddy cert, Fastly SNI,
      panel address, FCP apiUrl all follow). Legacy same-domain custom mode:
      leave `dns_hostname` clean (client-facing CNAME + Caddy cert); origin
      created separately in fastly/dns.yml (see 13).
    - Persist `fastly_origin_fqdn` to `{{ fastly_service_id_dir }}/fastly_origin_fqdn`
      next to `fastly_service_id` so change/migrate can delete the old origin record.
    Mirror the same derivation in `change/outline_change.yml`,
    `change/remnawave_change.yml` (after new-hostname generation, before DNS), and
    `migrate/migrate.yml` (destination side).
11. **Configurable client-facing fronting domains (LIST)** — new var
    `fastly_fronting_domains: []` (defaults/main.yml; list from day one, no
    singular var shipped → no later breaking change). When non-empty (requires
    `effective_cdn_provider == 'fastly'`):
    - `fastly_websocket_domain = fastly_fronting_domains | first` (the PRIMARY
      edge; flows to FCP `websocketDomain` — FCP's schema is single-valued, so
      multi-edge Outline keys are out of scope; Remnawave is unaffected, see
      Host loop below).
    - Assert: not combined with `fastly_edge_label`/`fastly_edge_random`
      (mutually exclusive edge selectors); treated as custom-mode for the
      TLS+CNAME flow (`effective_fastly_domain_mode` resolution updated in
      `tasks/main.yml` deploy validation).
    - Zone resolution PER DOMAIN: longest-suffix match of each entry against
      `domain_providers` keys → its fronting zone (`zone_id` + optional
      per-domain creds, same pattern as base_domain). Fail fast listing every
      uncovered domain.
    - service.yml: attach ALL fronting domains to the service (loop the
      version/domain POST; today single, line 245-260).
    - tls.yml: ONE TLS SUBSCRIPTION PER DOMAIN (independent lifecycle — one
      failed ACME challenge doesn't wedge the rest); parameterize the
      ACME-challenge DNS write + `cf_token` by each domain's resolved fronting
      zone (currently hardcoded to `base_domain`, tls.yml:120-160); resolve
      each domain's `cname_target` from its own subscription's activation.
    - fastly/dns.yml: per domain, write `domain → its cname_target` CNAME in
      THAT domain's fronting zone; the delete-A/AAAA-before-CNAME pattern
      applies per fronting name.
    - Backend SNI/override/cert = origin FQDN (Caddy cert covers only the
      origin; no clean-name cert or bootstrap records needed — same shape as
      shared mode).
    - Remnawave Hosts: loop transports × fronting domains — one Host per pair,
      remark `{{ random_hostname }}-{{ transport.name }}-{{ domain }}`,
      address/sni/host = that domain (create_hosts.yml; subscriptions then
      carry every edge → client failover). Single-domain case produces the
      same shape with one Host.
    - change/migrate: re-point EVERY fronting domain's CNAME to the new
      service's cname_target after TLS re-issue; never delete fronting CNAMEs
      (per-node public identity); delete only the OLD origin record.
    - Limitation (document): fronting domains are set at deploy; adding one to
      a live node = rotation (the service reuse-by-name path skips
      reconfigure). N domains = N TLS subscriptions (possible Fastly cost).
12. **Node address follows the origin** — `defaults/main.yml:313`:
    `remnawave_node_address: "{{ fastly_origin_fqdn | default(dns_hostname, true) }}"`.
    Fixes today's latent bug where Fastly *custom* mode registers
    `address = dns_hostname` (a CNAME to the edge — panel can't reach :2222 through
    Fastly). `register_node.yml` / `update_node.yml` pick it up unchanged.
    Node name stays `random_hostname` (already correct, `register_node.yml:36`).
    Reality path untouched (address = clean `dns_hostname`).
13. **Fastly service/origin labeling** (`tasks/providers/fastly/service.yml`) —
    - `fastly_service_name = random_hostname` in ALL modes (replaces edge-label /
      `fs-<hostname>` / `<hostname>-<domain>` naming at lines 54-75). Edge-domain
      facts (`fastly_websocket_domain`) unchanged except fronting-domain override.
    - Backend: `name: "{{ fastly_origin_name }}"`, `address: "{{ fastly_origin_fqdn }}"`
      (shared/fronting: replaces `dns_hostname` at line 84; legacy custom: replaces
      `origin-<hostname>`); healthcheck name follows.
    - SNI/override/cert host: shared + fronting = origin FQDN; legacy custom =
      clean `dns_hostname` (unchanged).
    - Note: lookup-by-name idempotency + half-built self-heal keep working
      (name is now node-unique). One-time transition: pre-change services use old
      names; rotation cleans them up by the persisted service-id file as today.
14. **fastly/dns.yml (custom modes)** — create origin records as
    `{{ fastly_origin_name }}` (replaces `origin-{{ random_hostname }}`,
    lines 31, 60, 93) in legacy custom mode; keep bootstrap-delete + CNAME cutover
    for that flavor; add the fronting-zone CNAME path (item 11); add deletion of
    the OLD origin record on rotation (read persisted `fastly_origin_fqdn`,
    tolerate absence; also delete legacy `origin-<old>` pattern for pre-change
    nodes).
15. **Docs** — README + CLAUDE.md naming-model section (table above), document
    `fastly_fronting_domains` with a `[video-streams.org, cdn-static.net]`
    example, note that `custom_hostname` is the intended way to get
    ops-meaningful names like `xray1-front-mci1-fs-ce`, and the CT-log caveat
    (suffix defeats zone enumeration / passive DNS, not crt.sh).

Verify: new offline test `tests/test_origin_identity.yml` (suffix shape,
shared-vs-custom-vs-fronting dns_hostname derivation, node-address fallback,
service/backend naming, per-domain longest-suffix zone match incl. multi-domain,
edge-selector mutual-exclusion table); extend `tests/test_fastly_edge.yml` for
service-name = hostname; `tests/run.sh`. Deploy integration can't exercise
Fastly (no account) — expression tests only, documented.

## P3 — Renames

16. **Config profile** — ✅ DONE (in the P1 pivot batch): `defaults/main.yml`
    `FreeSocks-CDN` → `FreeSocks-Config`; tests updated. Bootstrap is GET-by-name
    idempotent → re-running bootstrap on an existing panel creates a NEW profile;
    the old one is simply unreferenced (UUID-keyed everywhere). Document in
    README upgrade note (P4).
17. **Squad prefix** — ✅ DONE, superseded by the pivot: shared squads are
    statically named `FreeSocks-Fastly` / `FreeSocks-Reality` (no per-node
    suffix, no derivation needed). Legacy per-node path uses
    `remnawave_node_fronted_squad_prefix: "FSF"` / `"FSR"` with a ≤30-char
    assert. Old `FreeSocks-Fronted-*` squads on existing panels keep working
    (UUIDs already in FCP pools).

Verify: full `tests/run.sh` + integration (asserts the new names against the real panel).

## P4 — Doc-drift cleanup (from review)

18. CLAUDE.md: drop `fcp_bind_squad`/`admin:tiers:write` from Common Issues; fix
    provider-tree listing (add `gen_x25519`, `create_config_profile`, `create_squad`,
    `create_node_placement`, `teardown_node_placement`, `bind_placements`,
    `bind_default_mode`, `status_gate`); refresh squad/profile names; panel 2.8 note.
19. README.md: fix stale "reverse-proxies all traffic" Caddy prose (~:570-578);
    deploy-step order (DNS before install, ~:852-858); migrate backend-detection
    prose (~:879-885); full test-file list (~:1046-1055); cross-ref direction
    (~:538); add `slipstream_dns.yml` to directory structure.
20. `defaults/main.yml:3-8` header: add `bootstrap` to the mode list.

## P5 — FCP verification (done during planning — NO CHANGES NEEDED)

Explored `/Users/lunar/Documents/git/freesocks-control-plane`:
- FCP references squads/profiles **only by UUID** (pools in `appSettings`,
  `convex/lib/remnawavePlacement.ts`); squad names are display labels only
  (`AdminRemnawave.svelte:214`). Member-facing surfaces never show squad/server
  names (`convex/account.ts:498-501`).
- Mode pools are natively N-per-mode — adding `FreeSocks-Fastly-*` now and
  `FreeSocks-Cloudflare-*` later to `evade` works today, zero FCP changes.
- FCP never reads panel node names/addresses (`remnawave.ts:549-557`), never calls
  `/api/hosts`. `backendServers` name/slug are free-form (slug ≤64 chars) — a
  hostname like `xray1-front-mci1-fs-ce` fits.
- Renames need no seed/doc updates in the FCP repo (no name references found).

Optional FCP follow-ups (not required, not in scope): prune `remnawaveNodeStats`
rows for deleted squads; a third connection *mode* would be an FCP code change
(catalog + i18n); keep derived `location` codes ≤16 chars (`adminApi.ts:1176`).

## Open questions — ALL RESOLVED

1. ~~Separate fronting domain?~~ **RESOLVED** — yes, and LIST-valued:
   `fastly_fronting_domains`, decoupled per-domain zones (item 11).
   `global.ssl.fastly.net` being blocked + per-domain failover is the driver.
2. ~~Suffix persistence~~ **RESOLVED** — true random + persisted to
   `fastly_service_id_dir/fastly_origin_fqdn`. Deterministic derivation only
   protects until the vault leaks; random gets a fresh suffix per rotation; the
   role is already stateful. Caveat accepted: a failed-deploy retry can leave
   one orphan A record (harmless, same IP).
3. ~~Var rename~~ **RESOLVED** — keep `remnawave_bootstrap_fronted_squad_name`
   as the CDN-agnostic override; its DEFAULT becomes derived
   (`FreeSocks-{{ effective_cdn_provider | capitalize }}`, fallback
   `FreeSocks-Fastly`). No breakage, forward-compatible (item 17).
4. ~~Squad rename on rotation~~ **RESOLVED** — conditional: probe
   `PATCH /api/internal-squads/{uuid}` on the pinned CI panel; rename when
   supported, warn-only otherwise, never block rotation on it (item 8c).

## Definition of done (per phase)

- `bash tests/run.sh` green; `--syntax-check` on touched playbooks;
  `tests/run_integration.sh` green on phases touching panel/FCP/DNS logic (P1-6/8b,
  P3); CI workflow passes.
- No task-level chained `vars:` introduced (ansible-core 2.19+ rule); secrets and
  squad UUIDs `no_log`'d; new expressions covered by an offline `tests/test_*.yml`.
