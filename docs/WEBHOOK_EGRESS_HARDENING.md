# Webhook egress hardening (SSRF — the infrastructure layer)

Outbound webhooks make the **server** send HTTP requests to a URL an organization admin chose. Without
protection, that admin could aim the server at things only the server can reach: the cloud metadata
service (`169.254.169.254` → temporary cloud credentials), the database, other containers, admin ports.

## Two layers, on purpose

| Layer | Where | What it does | Why it isn't enough alone |
|---|---|---|---|
| 1. Application | `backend/app/services/url_safety.py` | HTTPS only; no credentials in URLs; refuses private / loopback / link-local / reserved / metadata addresses; resolves the name **once**, rejects the host if **any** answer is non-public, and connects to that exact validated IP (no second lookup → DNS-rebinding safe); never follows redirects; ignores proxy env vars | A bug, a new code path that forgets to call it, or a library quirk would silently remove the protection |
| 2. Network | firewall / egress proxy / network policy (this document) | Even if the app were tricked, the **network** refuses packets from the backend to internal ranges | Can't give the admin a readable error, and rules drift if nobody owns them |

Keep both. Layer 1 is code-reviewed and tested (`backend/tests/test_webhook_ssrf.py`); layer 2 is
configuration in your hosting environment, so it is **not enabled by this repository** — you must apply it
where you deploy. Nothing in the tests exercises it.

## Application settings (backend environment)

| Variable | Default | Meaning |
|---|---|---|
| `WEBHOOK_ALLOW_INSECURE_HTTP` | off | Also allow `http://` webhook URLs (all address checks still apply). Leave off in production. |
| `WEBHOOK_ALLOW_PRIVATE_TARGETS` | off | Lets a **developer** point a webhook at `localhost` / a private receiver. **Ignored when `ENVIRONMENT=production`** (a warning is logged). |
| `WEBHOOK_EGRESS_PROXY` | unset | Send all webhook deliveries through a dedicated egress proxy (see option A). The connection-level pinning cannot apply behind a proxy, so the proxy must enforce the address rules itself. |

## Layer 2 options (pick what matches where you host)

### A. A dedicated egress proxy (best when you can run one)
Run a forward proxy built for this (e.g. Stripe's open-source **Smokescreen**) on its own network
position that is allowed to reach the internet but **not** your private ranges, deny-list the private
ranges and metadata address in the proxy, and set `WEBHOOK_EGRESS_PROXY=http://smokescreen:4750`. The
backend then does not need general outbound access at all, only access to the proxy.

### B. Host firewall rules for Docker (`docker-compose.yml` deployments)
Docker evaluates the `DOCKER-USER` chain for container traffic. Replace `BACKEND_IP` with the backend
container's address and `COMPOSE_SUBNET` with the compose network's subnet (`docker network inspect`).
**Order matters:** `-I` puts a rule at the *top* of the chain, so insert the DROPs first and the ACCEPT
last — the ACCEPT for your own compose network (database, frontend) must sit *above* the DROPs, because
compose networks usually live inside `172.16.0.0/12`, which the DROPs also cover.

```sh
for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10 127.0.0.0/8; do
  iptables -I DOCKER-USER -s BACKEND_IP -d "$net" -j DROP
done
iptables -I DOCKER-USER -s BACKEND_IP -d COMPOSE_SUBNET -j ACCEPT   # inserted last => evaluated first
# IPv6 equivalents (fc00::/7, fe80::/10, ::1/128) with ip6tables
```
Check with `iptables -S DOCKER-USER` that the ACCEPT is listed before the DROPs, and test from inside the
container: `curl -m 5 http://169.254.169.254/` must fail while the backend still reaches the database.

### C. Kubernetes
A `NetworkPolicy` on the backend pods with an egress rule that allows `0.0.0.0/0` **except**
`10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, 100.64.0.0/10`, plus explicit allows for the
database and DNS.

### D. PaaS (Render etc.)
Private services on the same account/network are reachable from your backend by default. If the platform
offers outbound IP allow-listing, egress policies, or a dedicated "egress" service, use it; otherwise use
option A with a small proxy service so the backend itself is not trusted to choose destinations.

### Cloud metadata endpoint (do this regardless)
- **AWS:** require IMDSv2 and set the instance metadata hop limit to **1** so containers can't reach it.
- **GCP / Azure:** restrict or disable the metadata endpoint for workloads that don't need it, and give the
  service account the minimum permissions.
This limits the damage if some other bug ever lets an attacker make the server fetch a URL.

## How to verify (after deploying layer 2)
1. From the backend container/pod: `curl -m 5 http://169.254.169.254/` and `curl -m 5 http://<db-host>:5432` must fail (the DB is reached by the app through its normal path, not via the webhook sender).
2. Create a webhook to `https://169.254.169.254/` → must be refused with HTTP 400 (layer 1).
3. Create a webhook to a hostname you control that resolves to `10.x`/`127.0.0.1` → refused at save time; if you change its DNS afterwards, the **delivery** is blocked and shows *"…resolves … to a private or reserved address"* in the delivery list (layer 1, rebinding).
4. A webhook to a public URL that answers `302` → the delivery fails with *"Redirects are not followed"* and is not retried.
