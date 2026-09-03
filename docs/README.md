# Documentation

Pick the guide for what you're doing. Each assumes a different level of expertise.

| I want to… | Read | Expertise assumed |
|---|---|---|
| **Use the dashboard** on my phone — read the cards, get alerts, control a switch | [User guide](user-guide.md) | None |
| **Commission a site** — set up the gateway box, connect plugs/sensors/meters, hand over a branded dashboard | [Installer guide](installer-guide.md) | Field tech: comfortable with a terminal, SSH, and wiring |
| **Run the platform** — brokers, credentials, the data store, upgrades | [Operations guide](operations-guide.md) | Sysadmin: Docker, DNS/TLS, secrets |
| **Host it as a service** — stand up the multi-tenant cloud, onboard clients | [Cloud hosting guide](cloud-hosting.md) | Sysadmin: a VPS, DNS, secrets |
| **Build on the platform** — write an adapter for a new device, or consume the data API | [Developer guide](developer-guide.md) | Software developer: Python, MQTT, HTTP |
| **Build a device** — ESP32 firmware that speaks the contract natively (path C) | [ESP32 firmware guide](firmware-esp32.md) | Embedded developer: MicroPython/C, ESP32 |

Background reading, in order of usefulness:

- [How real devices connect](device-integration.md) — the architecture argument: why one
  canonical contract + per-*family* bridges means a new device is usually a config row, not code.
- [OTA firmware updates](ota.md) — the per-family update/rollback design.
- [ESP-IDF port plan](esp-idf-port-plan.md) — planned C firmware for our own hardware: why, milestones, and the preconditions for starting.
- [`../ROADMAP.md`](../ROADMAP.md) — where the platform is, phase by phase.
- [`../catalog/`](../catalog/) — the qualified device catalog: one record per benched model,
  including gate results, quirks, and procurement notes.
