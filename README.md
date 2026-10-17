# ansible-role-freesocks

[![CI](https://github.com/unredacted/ansible-role-freesocks/actions/workflows/ci.yml/badge.svg)](https://github.com/unredacted/ansible-role-freesocks/actions/workflows/ci.yml)

An Ansible role that bootstraps a [FreeSocks](https://freesocks.org/) proxy node (a Remnawave / Xray-core node) and enrolls it with the [FreeSocks Control Plane](https://freesocks.org/) (FCP).

**Version 2 does one thing: the machine.** It installs Docker and the node container, Caddy and a decoy site when the mode wants one, lets in the ports FCP lists for the node, and tells FCP "here I am". FCP owns everything on the backend (the profile, its transports, the mode groups, the addresses), the origin DNS record of a WebSocket node, the fronting (Cloudflare, Fastly, L4 edges) and the release to members. The role never talks to the backend: the node secret comes from FCP with the machine configuration. This is the bootstrap contract v2 (FCP `docs/servers.md`, "Node lifecycle").

Version 1 (Outline, hostname rotation, backend-to-backend migration, the role writing the backend) lives on the `v1` tag.

## Modes

A node serves one **connection mode**: one of the modes the backend was set up for in FCP (Admin -> Servers, "Set up this backend"). The role is told the mode (`node_mode`) and nothing else about the shape: FCP's bootstrap answer says whether Caddy and which routes are wanted.

| Mode (the defaults) | What the machine runs                                       | What FCP does after                                 |
| ------------------- | ----------------------------------------------------------- | --------------------------------------------------- |
| `privacy-reality`   | REALITY on 443                                              | creates its addresses for members, tests, releases  |
| `freedom-reality`   | REALITY on 443, reached through an L4 edge                  | an L4 edge in front of it                           |
| `freedom-xhttp`     | XHTTP under REALITY on 443, reached through an L4 edge      | an L4 edge in front of it                           |
| `freedom-ws`        | WebSocket on loopback behind Caddy (TLS for the origin name) | origin DNS record, an L7 edge (Cloudflare or Fastly) |

A node never reaches a member before an operator approves it in Admin -> Servers. A backend that already has nodes is adopted in FCP, never re-bootstrapped: the role is only ever run for a **new** machine.

## Requirements

- Debian 12 or 13, Python 3, ports **80 and 443 free** on a node whose mode runs Caddy (HTTP-01 on 80, TLS on 443)
- Controller: ansible-core >= 2.15 and the collections in `requirements.yml`:
  ```sh
  ansible-galaxy collection install -r requirements.yml
  ```
- In FCP: the panel registered under Backend servers, **Set up this panel** done (Admin -> Servers), and a token for the role (below).
- A host firewall the role can keep its rules in (ufw, firewalld or nftables), or none: see [Firewall](#firewall). SSH, the node API port (2222) and port 80 on a Caddy node stay yours to open.

## Quick start

### 1. A token

The role only uses its node's own routes (`/api/v1/admin/servers/{slug}/nodes/by-name/{name}`, FCP `docs/servers.md` "Node registration (contract v2)"). Either the fleet token (`admin:servers:write`, which reads the node's view too) or, better, an `admin:edges:register` token minted with a registration boundary: this backend and this node name. `admin:servers:manage` is no role token: FCP bounds it like the register scope, so on its own it acts for no node and never reaches the node secret. From the control plane (through the deployer container, never a bare `bunx`):

```bash
docker compose -f docker-compose.stack.yml --env-file .env.beta run --rm --no-deps --entrypoint bash deployer -c 'export CONVEX_SELF_HOSTED_ADMIN_KEY="$(grep "|" /keys/admin_key | tail -n1 | tr -d "[:space:]")" && bunx convex run adminApi:mintAutomationToken '"'"'{"scopes":["admin:edges:register"],"registerBackendSlugs":["remnawave-primary"],"registerNodeNames":["quiet-falcon-nest"]}'"'"''
```

Vault it as `fcp_api_token`. Add `admin:status:read` to its scopes if you turn on `fcp_status_gate`.

### 2. Deploy

```yaml
# group_vars/nodes.yml
fcp_api_url: "https://fcp.example.org"
fcp_api_token: "{{ vault_fcp_api_token }}"
fcp_remnawave_panel_slug: "remnawave-primary"
remnawave_caddy_email: "ops@example.org"
```

```sh
# a direct node
ansible-playbook playbook.yml -e target=node1 -e operation_mode=deploy -e node_mode=privacy-reality

# a WebSocket node (FCP makes its origin name; or set node_origin_hostname)
ansible-playbook playbook.yml -e target=node2 -e operation_mode=deploy -e node_mode=freedom-ws

# an edge-fronted REALITY node on a tailnet: the backend dials the overlay address, edges dial the public one
ansible-playbook playbook.yml -e target=node3 -e operation_mode=deploy -e node_mode=freedom-reality \
  -e node_address_interface=tailscale0 -e node_public_address=203.0.113.7
```

The run persists the node's name on the machine first, enrolls the node, fetches its machine configuration and secret, lets in the ports FCP lists for it, starts the container (and Caddy when FCP declared an ingress, or takes an earlier run's Caddy out of the way when it declared none), reports the applied revision and which ports the firewall lets in, and waits for FCP to find the machine ready. Then, in FCP: **Edges -> Protect a node** for an edge-fronted mode, and **Servers -> the node -> Approve and activate**.

Running `deploy` again is how a machine change FCP made (a route, a port) is applied: the role reports only what it observes; FCP owns the settings. A run that stopped halfway resumes against the same node: the name and mode are read back from the machine, so later runs need neither. The mode is written only once FCP has accepted it, so a mode the backend is not set up for is corrected by running again with the right one.

A node an admin renamed in FCP keeps answering under its old name. The role follows the rename: the run goes on under the new name, the machine carries it (`.node_name`, the old one kept in `.node_former_names`), and an inventory that still says the old name resolves to the new one. A token bounded to several backends keeps acting under the old name only (FCP extends a boundary to the new name only for a token bounded to this backend alone); the run then goes on under the old name and says so.

A machine deployed before modes existed carries a purpose instead (`direct`, `front`, `relay`). Its mode is derived from `fcp_legacy_purpose_modes` (`privacy-reality`, `freedom-ws`, `freedom-reality`) and written on the first v2 run, so such a node stays deployable; pass `-e node_mode=...` when the backend names those modes otherwise.

### 3. Update, wipe

```sh
ansible-playbook playbook.yml -e target=node1 -e operation_mode=update   # new image, re-report
ansible-playbook playbook.yml -e target=node1 -e operation_mode=wipe     # retire
```

`wipe` asks FCP to retire the node. A node that was ever live (or has anything of it still out there) stops the run until an operator decides its retirement in Servers; afterwards FCP tears down what it made, the machine is cleaned up, and FCP is told.

## Firewall

FCP moves a transport to another port only once every node serving it says its firewall lets the new port in (FCP `docs/servers.md` "Firewall acknowledgement"). The node's view and the bootstrap answer list `firewall.ports`: each port the node serves (`serving`) and each port a staged change waits for on this node (`pending`). On every `deploy` and `update` run the role lets each listed port in (TCP, from anywhere) before the node starts and before it reports, then names in the applied report the ports it can stand behind. That report is what confirms a waiting change.

**Where the rules go** (`fcp_firewall_backend`). The role keeps one object in the host firewall and never touches anything else: no rule of yours, no default policy, and it never installs, enables, starts or flushes a firewall.

| Backend     | What the role owns                                                                                          |
| ----------- | ----------------------------------------------------------------------------------------------------------- |
| `ufw`       | The application profile `freesocks-fcp` (`/etc/ufw/applications.d/`) and the one rule `ufw allow freesocks-fcp`. On an inactive ufw the rule waits for it. |
| `firewalld` | The service `freesocks-fcp` (`/etc/firewalld/services/`) in `fcp_firewall_firewalld_zone` (default: the default zone), permanent and runtime. A change to the list reloads firewalld. |
| `nftables`  | The set `freesocks_fcp` in `fcp_firewall_nftables_file`, which your ruleset includes once (below). The role rewrites the file and the live set in one transaction, never reloading your ruleset. |
| `none`      | Nothing.                                                                                                    |
| `auto`      | The default: firewalld if it runs, else ufw if it is active, else nftables if `nftables.service` is active, else `none`. |

An `accept` in a table of the role's own would not get past your input chain's drop, so with nftables the set lives in your table. Wire it once, then load the ruleset (the role writes the file first, so it exists before you include it):

```
table inet filter {
    include "/etc/nftables.d/freesocks-fcp.nft"
    chain input {
        type filter hook input priority filter; policy drop;
        ...
        tcp dport @freesocks_fcp accept
    }
}
```

The set counts as wired only when a chain on the input hook of that table holds exactly `tcp dport @freesocks_fcp accept` (a counter allowed); a mention anywhere else (a drop, a chain off the hook) does not. An explicit `nftables` whose ruleset does not use the set stops the run and says how to wire it; under `auto` the role reports nothing for it instead.

**Closing.** A port FCP no longer lists (the old port once a change is sent) leaves the role's object. SSH (22 and `ansible_port`), the node API port and `fcp_firewall_protected_ports` never do, and an empty list from FCP is taken as a gap, not as "close everything". `wipe` empties the object (protected ports kept).

**Switching backends.** The machine records which backends hold the role's rules (`/opt/remnanode/.firewall_backends`). When the backend changes (you set another, or `auto` now finds another firewall), the object in the old one is pruned on every run as FCP stops listing its ports, never added to, and forgotten once empty; `wipe` empties it too. A stopped firewalld is remembered and pruned once it runs again.

**What the report claims.** A listed port is reported open when the role's own rule took effect, or when you vouch for it (`fcp_firewall_assume_open`). With `none` (or nothing found by `auto`) the role opened nothing, and a firewall it cannot see, such as a provider's, may still drop a port: it reports only what you vouch for, and otherwise leaves `firewall` out of the report and says that the change waits on this node. Behind a provider firewall that filters (a cloud security group) set `fcp_firewall_provider_filter: true`: the role still opens its own rules but reports only what `fcp_firewall_assume_open` vouches for. A change staged while a run reports waits for the next run, which the run says.

| Variable                        | Default                              | What                                                                                   |
| ------------------------------- | ------------------------------------ | -------------------------------------------------------------------------------------- |
| `fcp_firewall_backend`          | `auto`                               | `auto`, `ufw`, `firewalld`, `nftables` or `none`                                       |
| `fcp_firewall_assume_open`      | `false`                              | What you vouch is open: `true` (every listed port), a list of ports (`-e fcp_firewall_assume_open=443,8443`), or `false` |
| `fcp_firewall_provider_filter`  | `false`                              | A provider firewall also filters the machine: report only what is vouched for          |
| `fcp_firewall_protected_ports`  | `[]`                                 | Ports never closed on the role's account, besides SSH and the node API port            |
| `fcp_firewall_firewalld_zone`   | the default zone                     | The firewalld zone the role's service goes in                                          |
| `fcp_firewall_nftables_table`   | `inet filter`                        | The family and table your input chain is in                                            |
| `fcp_firewall_nftables_file`    | `/etc/nftables.d/freesocks-fcp.nft`  | The role's set file                                                                    |

No firewall rule the role writes logs anything (the no-log posture of `remnawave_node_nftables_logging`).

### Upgrading: port changes wait on older nodes

A role that predates this release sends no `firewall` in its applied report, and FCP confirms nothing from such a report. So **until every node serving a transport runs this release, an FCP port change on that transport waits**, and the Servers page ("Port changes") names the node it waits on. Run `deploy` (or `update`) with this release on those nodes before changing a port; a node no role runs (adopted) is confirmed by an admin on the Servers page. Against an FCP that predates the firewall acknowledgement (no `firewall` in its answers) the role leaves the host firewall alone and reports nothing about it.

## Variables

| Variable                              | Default                | What                                                                      |
| ------------------------------------- | ---------------------- | ------------------------------------------------------------------------- |
| `operation_mode`                      |                        | `deploy`, `update` or `wipe`                                              |
| `node_mode`                           | persisted              | The connection mode it serves (set up in FCP); taken at the first deploy   |
| `fcp_legacy_purpose_modes`            | the built-in modes     | The mode a machine's `direct`/`front`/`relay` purpose becomes              |
| `node_name`                           | generated              | The panel node name (3 to 30 plain characters); persisted in `.node_name` |
| `node_machine_name`                   | `inventory_hostname`   | Sent at enrollment so FCP shows operators which machine a node is; never shown to members; empty = not sent |
| `node_label`                          | from the name          | The DNS label of a managed origin name (WebSocket)                        |
| `node_address_interface`              |                        | The interface whose address the panel dials (e.g. `tailscale0`)           |
| `node_management_address`             |                        | Or the name the panel dials                                               |
| `node_public_address`                 | default route          | The public IPv4 (members, the origin record, edges); set it behind NAT    |
| `node_publish_ipv6`                   | `false`                | Report the v6 for the origin record (WebSocket)                           |
| `node_origin_hostname`                |                        | The origin name when FCP does not manage the zone (WebSocket)             |
| `fcp_api_url`, `fcp_api_token`        |                        | FCP and its token (vault)                                                 |
| `fcp_remnawave_panel_slug`            | `remnawave-primary`    | The panel's slug in FCP                                                   |
| `fcp_wait_machine_ready`              | `true`                 | Wait for FCP's verification after the applied report                      |
| `fcp_status_gate`                     | `false`                | At the end, wait for the node's own view to be healthy, then the backend row (`admin:status:read`) |
| `fcp_firewall_*`                      |                        | See [Firewall](#firewall)                                                  |
| `fcp_registration_retries` / `_delay` | `60` / `5`             | Polling the node's view                                                   |
| `remnawave_node_image`                | `remnawave/node:3.4.2` | The node container                                                        |
| `remnawave_caddy_email`               |                        | ACME registration (a mode that runs Caddy)                                |
| `remnawave_caddy_listen_port`         | `443`                  |                                                                           |
| `remnawave_decoy_root`                | `/var/www/decoy`       |                                                                           |

## What the role writes on the machine

`/opt/remnanode/docker-compose.yml` (0640, carries the node secret), `.node_name` (written before the first FCP call), `.node_mode` (written once FCP accepted it), `.node_former_names` (after a rename), `.firewall_backends` (which firewalls hold the role's rules), `.applied_revision`; `/etc/logrotate.d/remnanode`; when the mode runs Caddy `/etc/caddy/Caddyfile` and the decoy under `/var/www/decoy`; the role's firewall object (the ufw profile `freesocks-fcp`, the firewalld service `freesocks-fcp`, or the nftables set file). The node's country lives in FCP (the node's Settings), not here.

## Testing

```sh
tests/run.sh
```

Offline: the bootstrap contract against `tests/mock_fcp.py` (`test_register.yml`: enrollment, the firewall report and a port change waiting on the node, renames, FCP's scope rules, the status gate), the retirement contract (`test_wipe.yml`), each firewall backend against a fake host firewall (`test_firewall.yml`, `tests/fake_firewall.py`: what the role renders and runs, idempotence, pruning only its own object, `none` with and without vouching), the name and mode resolution including a machine that carries a legacy purpose or a former name (`test_identity.yml`), the Caddyfile and compose renders, the management address resolution. CI runs the same.

## License

GPL-3.0-only

## Author Information

lunarthegrey, Unredacted Inc
