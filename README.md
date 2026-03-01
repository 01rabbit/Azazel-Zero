# AZ-02 Azazel-Gadget - Personal Tactical Defense Device

> **Codename:** `TACMOD`

<p align="center">
  <a href="./README_ja.md">
    <img alt="日本語" src="https://img.shields.io/badge/Language-日本語-2ea44f?style=for-the-badge">
  </a>
  <a href="./README.md">
    <img alt="English" src="https://img.shields.io/badge/Language-English-1f6feb?style=for-the-badge">
  </a>
</p>

Azazel-Gadget (formerly Azazel-Zero) is a portable defensive gateway for untrusted Wi-Fi environments on Raspberry Pi Zero 2 W / Pi 4-class devices.

<p align="center">
  <img src="images/Azazel-Gadget_logo.png" alt="Azazel-Gadget logo" width="540">
</p>
<p align="center">
  <img src="https://img.shields.io/badge/-Raspberry%20Pi-C51A4A.svg?logo=raspberry-pi&style=flat">
  <img src="https://img.shields.io/badge/-Python-F9DC3E.svg?logo=python&style=flat">
  <img src="https://img.shields.io/badge/-Flask-000000.svg?logo=flask&style=flat">
  <img src="https://img.shields.io/badge/Javascript-276DC3.svg?logo=javascript&style=flat">
  <img src="https://img.shields.io/badge/-HTML5-333.svg?logo=html5&style=flat">
  <img src="https://img.shields.io/badge/-CSS3-1572B6.svg?logo=css3&style=flat">
</p>

## Concept

Azazel-Gadget is a "sacrificial shield" you can carry at all times.
It is a personal tactical device designed for low-trust networks such as public Wi-Fi, built to stand in front of your endpoint and take the first hit directly.

This is not a generic security product that merely blocks traffic. It is structured around attacker behavior: minimizing the exposed defense surface, blocking lateral movement, and controlling exposure when needed. The default posture is full defense (`Shield`), but it can switch to an observational defense posture (`Scapegoat`) when the situation demands it.

Do not defend blindly; defend with awareness of the attack.
Azazel-Gadget is a dedicated defensive appliance that still requires tactical judgment. It stays quiet in normal conditions, shifts posture on anomalies, and when necessary accepts attacks, observes them, and buys time.

It is a front-line device designed for daily carry.
Not invisible security, but a shield you consciously raise.

## Hardware Variants

| Azazel-Gadget Portable | Azazel-Gadget Dock |
|---|---|
| Raspberry Pi Zero 2 W implementation<br>![Azazel-Gadget Portable on Raspberry Pi Zero W](images/Azazel-Gadget_Portable.png) | Raspberry Pi 3/4/4B implementation<br>![Azazel-Gadget Shield on Raspberry Pi 4](images/Azazel-Gadget_Shield.png) |

## Interface Preview

| Web UI | Unified TUI |
|---|---|
| [![Azazel-Gadget Web UI screenshot](images/WebUI.png)](images/WebUI.png) | [![Azazel-Gadget unified TUI screenshot](images/TUI.png)](images/TUI.png) |

Azazel-Gadget combines active network defense, operator-facing interfaces, and optional deception services into a compact gateway workflow.

## Features

### 1) First-minute control plane
- Main controller tracks upstream health, captive portal status, and risk transitions (`NORMAL`/`DEGRADED`/`CONTAIN`).
- Writes UI snapshot JSON consumed by Web UI and TUI.
- Exposes local Status API (`:8082`) with action endpoints (`/action/*`) and details endpoint (`/details`).
- Includes tactics decision logging (`decision_explanations.jsonl`) and ntfy notifier hooks.
- In `DECEPTION`, applies `tc` delay only to Suricata-confirmed OpenCanary attack flows (targeted Delay-to-Win).

### 2) Action/control daemon (Unix socket)
- Dedicated daemon at `/run/azazel/control.sock`.
- Executes action scripts, Wi-Fi scan/connect handlers, and portal-viewer startup workflow.
- Proxies deterministic mode switching (`mode_set`, `mode_status`) to `azctl`.
- Supports control-plane snapshot streaming (`watch_snapshot`) for clients.
- Includes path-schema actions (`path_schema_status`, `migrate_path_schema`).

### 3) Web UI backend (Flask)
- Dashboard + state API + SSE state stream.
- Action API (new and legacy format), Wi-Fi scan/connect API, portal-viewer APIs.
- ntfy event bridge SSE endpoint.
- CA certificate metadata/download endpoints for local HTTPS onboarding.
- Token auth via header or query (if token file exists).

### 4) Portal viewer (noVNC)
- Browser-assisted captive-portal workflow (Chromium + Xvfb + x11vnc + noVNC).
- Web UI can start and open it on demand (`/api/portal-viewer/open`).
- Runtime start URL override is supported.

### 5) E-paper integration
- Boot/shutdown splash service.
- Periodic captive-portal detection display updates.
- Suricata-linked e-paper alert updates.
- Mode/state refresh from `/run/azazel/epd_state.json` (event-driven + periodic timer).

### 6) Optional local monitoring/deception
- OpenCanary systemd unit and startup wrapper.
- Suricata integration and monitoring-state reflection in UI.
- Lightweight canary rules detect scans/access attempts to `22/80` from same-LAN peers on `wlan0` as well as non-local sources.
- Optional local ntfy server (`--with-ntfy`) and `/api/events/stream` bridge.

## Architecture at a Glance

0. `azazel-mode.service` + `azctl`
- Applies boot/default mode as `shield` (ignores persisted mode at boot).
- Single applicator for firewall/sysctl/OpenCanary orchestration.
- Writes audit log and EPD state.

1. `azazel-first-minute.service`
- Produces state snapshot and Status API (`:8082`).
2. `azazel-control-daemon.service`
- Bridges action requests over `/run/azazel/control.sock`.
3. `azazel-web.service`
- Flask backend (`127.0.0.1:8084` by default), reads snapshot and calls control daemon/Status API.
4. Optional `caddy.service` (installer `--with-webui`)
- TLS reverse proxy (`https://<MGMT_IP>:443`) to Flask backend.
5. Optional `azazel-portal-viewer.service`
- noVNC endpoint (default `10.55.0.10:6080`), started on demand by API.

## Modes

| Mode | Behavior | EPD Sample |
|---|---|---|
| `portal` | Internet gateway behavior for `usb0` clients (NAT via `wlan0`). Decoy exposure is OFF on `wlan0`. | ![Portal mode EPD sample](images/portal_composite.png) |
| `shield` (default) | Drops inbound from `wlan0` while keeping `usb0` client outbound path. Decoy exposure is OFF. | ![Shield mode EPD sample](images/shield_composite.png) |
| `scapegoat` | Exposes only OpenCanary allowlisted ports on `wlan0`. OpenCanary runs in isolated namespace (`az_canary`) and never gets a path to `usb0`. | ![Scapegoat mode EPD sample](images/scapegoat_composite.png) |

Warning display (not a mode):

| Display | Trigger | EPD Sample |
|---|---|---|
| `WARNING` | Alert conditions detected by the monitoring pipeline. | ![Warning EPD sample](images/warning_composite.png) |

Single source of truth:
- `/etc/azazel/mode.json` (compatibly linked to `/etc/azazel-gadget/mode.json`)
- Volatile runtime EPD/status state: `/run/azazel/epd_state.json`
- Audit log: `/var/log/azazel/mode_changes.jsonl`

CLI:

```bash
sudo azctl mode status
sudo azctl mode set shield
sudo azctl mode set portal
sudo azctl mode set scapegoat
```

## Included Services (systemd)

| Unit | Purpose |
|---|---|
| `azazel-mode.service` | Boot mode applicator (`azctl mode apply-default`) |
| `azazel-first-minute.service` | Main control-plane process |
| `azazel-control-daemon.service` | Unix socket action daemon |
| `azazel-web.service` | Flask backend API/UI |
| `azazel-portal-viewer.service` | Captive-portal viewer (noVNC) |
| `usb0-static.service` | Forces static IPv4 on `usb0` |
| `azazel-nat.service` | iptables-based forwarding/NAT helper |
| `azazel-epd.service` | E-paper startup status |
| `azazel-epd-refresh.service` + `.timer` | Mode/state EPD refresh pipeline |
| `azazel-epd-shutdown.service` | E-paper shutdown clear/splash |
| `azazel-epd-portal.service` + `.timer` | E-paper captive-portal checks |
| `suri-epaper.service` | Suricata-driven E-paper updates |
| `opencanary.service` | Optional deception service |
| `opencanary@.service` | OpenCanary in a dedicated network namespace |

## Installation Options

Main entrypoint: `install.sh`

| Option | Effect |
|---|---|
| `--with-webui` | Installs Flask venv + Caddy HTTPS reverse proxy |
| `--with-canary` | Installs/enables OpenCanary |
| `--with-ntfy` | Installs local ntfy server (`:8081`) |
| `--with-portal-viewer` | Installs noVNC/Chromium stack |
| `--with-epd` | Enables Waveshare E-Paper dependencies (default ON) |
| `--all` | Enables all optional features above |
| `--resume` | Resume after reboot-required network stage |

Typical:

```bash
sudo ./install.sh --all
# if prompted for reboot:
sudo ./install.sh --resume
```

## Web API

| Endpoint | Notes |
|---|---|
| `GET /` | Dashboard HTML |
| `GET /api/state` | Snapshot + monitoring + portal-viewer state |
| `GET /api/mode` | Current mode metadata |
| `POST /api/mode` | Switch mode (`portal`/`shield`/`scapegoat`) |
| `GET /api/state/stream` | SSE state stream |
| `GET /api/events/stream` | SSE ntfy bridge events |
| `GET /api/portal-viewer` | noVNC state/URL |
| `POST /api/portal-viewer/open` | Start/open portal viewer |
| `POST /api/action` | New action format |
| `POST /api/action/<action>` | Legacy action format |
| `GET /api/wifi/scan` | Wi-Fi scan (no token required by default) |
| `POST /api/wifi/connect` | Wi-Fi connect |
| `GET /api/certs/azazel-webui-local-ca/meta` | Local CA metadata |
| `GET /api/certs/azazel-webui-local-ca.crt` | Local CA download |
| `GET /health` | Web backend health |

Allowed actions (API): `refresh`, `reprobe`, `contain`, `release`, `details`, `stage_open`, `disconnect`, `wifi_scan`, `wifi_connect`, `portal_viewer_open`, `mode_set`, `mode_status`, `mode_get`, `mode_portal`, `mode_shield`, `mode_scapegoat`, `shutdown`, `reboot`

Token auth:
- Header: `X-AZAZEL-TOKEN` or `X-Auth-Token`
- Query: `?token=...`

## Interfaces

- Web UI: `azazel_web/`
- Unified TUI monitor/menu: `py/azazel_gadget/cli_unified.py`
- Menu compatibility launcher: `py/azazel_menu.py`
- Terminal status panel: `py/azazel_status.py`
- E-paper renderer/controller: `py/azazel_epd.py`, `py/boot_splash_epd.py`

## EPD Indicators

- Transition:
  - During mode apply: `mode=switching`, target mode shown.
  - On failure: temporary `FAILED` state then restored steady mode.
- Steady:
  - `PORTAL` / `SHIELD` / `SCAPEGOAT` mode reflected from `/run/azazel/epd_state.json`.
  - Includes internet/DHCP/DNS/OpenCanary fields and exposed decoy ports for scapegoat.

## Security Guarantees

- No new inbound path from `wlan0` to `usb0` in any mode.
- `shield`: inbound `wlan0` traffic dropped by dedicated mode firewall table.
- `portal`: NAT for `usb0` clients without decoy exposure.
- `scapegoat`: only OpenCanary allowlisted ports exposed; canary stack isolated in `az_canary`.

## Known Limits

- Scapegoat netns exposure uses host-level DNAT/forwarding; ensure upstream `wlan0` remains stable.
- Mode post-check internet test is host-side (`ping/getent`) and not a full usb0 client synthetic transaction.
- If EPD hardware/driver is unavailable, mode switch still succeeds and only journals EPD refresh errors.

## Testing

- Unit tests: `tests/`
- Regression scripts: `scripts/tests/regression/`
- UI stack smoke test: `scripts/tests/e2e/run_ui_stack_smoke.sh`

## Path Compatibility

Both naming schemas are supported:
- Current: `azazel-gadget` (`/etc/azazel-gadget`, `/run/azazel-gadget`, `~/.azazel-gadget`)
- Legacy: `azazel-zero` (`/etc/azazel-zero`, `/run/azazel-zero`, `~/.azazel-zero`)

Schema helpers and migration are implemented in `py/azazel_gadget/path_schema.py`.
Legacy path compatibility is planned through `2026-12-31`.

## Operational Notes

- State consistency across interfaces: Web UI and TUI use the same snapshot model, and EPD refresh prioritizes the same runtime state source.
- USB NAT indicator: `connection.usb_nat` is based on active routing/NAT state, so it reflects actual forwarding availability rather than interface-name guesses.
- Log verbosity control: `AZAZEL_DEBUG` is treated as a strict boolean (`1/true/yes/on` to enable debug, `0/false/no/off` to disable).

## Repository structure (main)

| Path | Meaning |
|---|---|
| `py/azazel_gadget/` | Controller, sensors, tactics engine, path schema |
| `py/azazel_control/` | Control daemon + Wi-Fi handlers + action scripts |
| `azazel_web/` | Flask backend + static dashboard |
| `systemd/` | Service and timer units |
| `installer/` | Staged installer |
| `configs/` | Default runtime configs |
| `scripts/` | Runtime helpers + tests |
| `docs/` | Development/archive notes |

## License

See `LICENSE` if present in this repository.
