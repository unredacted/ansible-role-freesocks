# ansible-role-freesocks

[![CI](https://github.com/unredacted/ansible-role-freesocks/actions/workflows/ci.yml/badge.svg)](https://github.com/unredacted/ansible-role-freesocks/actions/workflows/ci.yml)

An Ansible role that bootstraps a [FreeSocks](https://freesocks.org/) proxy node (a Remnawave / Xray-core node) and enrolls it with the [FreeSocks Control Plane](https://freesocks.org/) (FCP).

**Version 2 does one thing: the machine.** It installs Docker and the node container, Caddy and a decoy site when the mode wants one, and tells FCP "here I am". FCP owns everything on the backend (the profile, its transports, the mode groups, the addresses), the origin DNS record of a WebSocket node, the fronting (Cloudflare, Fastly, L4 edges) and the release to members. The role never talks to the backend: the node secret comes from FCP with the machine configuration. This is the bootstrap contract v2 (FCP `docs/servers.md`, "Node lifecycle").

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

## Quick start

### 1. A token

Either the fleet token (`admin:servers:write`) or, better, a register-only token confined to this panel and node name. From the control plane (through the deployer container, never a bare `bunx`):

```bash
docker compose -f docker-compose.stack.yml --env-file .env.beta run --rm --no-deps --entrypoint bash deployer -c 'export CONVEX_SELF_HOSTED_ADMIN_KEY="$(grep "|" /keys/admin_key | tail -n1 | tr -d "[:space:]")" && bunx convex run adminApi:mintAutomationToken '"'"'{"scopes":["admin:edges:register"],"registerBackendSlugs":["remnawave-primary"],"registerNodeNames":["quiet-falcon-nest"]}'"'"''
```

Vault it as `fcp_api_token`.

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

The run persists the node's name and mode on the machine first, enrolls the node, fetches its machine configuration and secret, starts the container (and Caddy when FCP declared an ingress), reports the applied revision, and waits for FCP to find the machine ready. Then, in FCP: **Edges -> Protect a node** for an edge-fronted mode, and **Servers -> the node -> Approve and activate**.

Running `deploy` again is how a machine change FCP made (a route, a port) is applied: the role reports only what it observes; FCP owns the settings. A run that stopped halfway resumes against the same node: the name and mode are read back from the machine, so later runs need neither.

### 3. Update, wipe

```sh
ansible-playbook playbook.yml -e target=node1 -e operation_mode=update   # new image, re-report
ansible-playbook playbook.yml -e target=node1 -e operation_mode=wipe     # retire
```

`wipe` asks FCP to retire the node. A node that was ever live (or has anything of it still out there) stops the run until an operator decides its retirement in Servers; afterwards FCP tears down what it made, the machine is cleaned up, and FCP is told.

## Variables

| Variable                              | Default                | What                                                                      |
| ------------------------------------- | ---------------------- | ------------------------------------------------------------------------- |
| `operation_mode`                      |                        | `deploy`, `update` or `wipe`                                              |
| `node_mode`                           | persisted              | The connection mode it serves (set up in FCP); taken at the first deploy   |
| `node_name`                           | generated              | The panel node name (3 to 30 plain characters); persisted in `.node_name` |
| `node_label`                          | from the name          | The DNS label of a managed origin name (WebSocket)                        |
| `node_address_interface`              |                        | The interface whose address the panel dials (e.g. `tailscale0`)           |
| `node_management_address`             |                        | Or the name the panel dials                                               |
| `node_public_address`                 | default route          | The public IPv4 (members, the origin record, edges); set it behind NAT    |
| `node_publish_ipv6`                   | `false`                | Report the v6 for the origin record (WebSocket)                           |
| `node_origin_hostname`                |                        | The origin name when FCP does not manage the zone (WebSocket)             |
| `fcp_api_url`, `fcp_api_token`        |                        | FCP and its token (vault)                                                 |
| `fcp_remnawave_panel_slug`            | `remnawave-primary`    | The panel's slug in FCP                                                   |
| `fcp_wait_machine_ready`              | `true`                 | Wait for FCP's verification after the applied report                      |
| `fcp_registration_retries` / `_delay` | `60` / `5`             | Polling the node's view                                                   |
| `remnawave_node_image`                | `remnawave/node:3.4.1` | The node container                                                        |
| `remnawave_caddy_email`               |                        | ACME registration (a mode that runs Caddy)                                |
| `remnawave_caddy_listen_port`         | `443`                  |                                                                           |
| `remnawave_decoy_root`                | `/var/www/decoy`       |                                                                           |

## What the role writes on the machine

`/opt/remnanode/docker-compose.yml` (0640, carries the node secret), `.node_name` and `.node_mode` (written before the first FCP call), `.applied_revision`; `/etc/logrotate.d/remnanode`; when the mode runs Caddy `/etc/caddy/Caddyfile` and the decoy under `/var/www/decoy`. The node's country lives in FCP (the node's Settings), not here.

## Testing

```sh
tests/run.sh
```

Offline: the bootstrap contract against `tests/mock_fcp.py` (`test_register.yml`), the retirement contract (`test_wipe.yml`), the Caddyfile and compose renders, the management address resolution. CI runs the same.

## License

GPL-3.0-only

## Author Information

lunarthegrey, Unredacted Inc
