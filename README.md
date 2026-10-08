# IoT Box Community

`community_iot_box` is the free, LGPL-3 Odoo 17 Community module maintained by
JDA SOLUTIONS.

It provides the Odoo-side foundation for a Community IoT stack:

- IoT Boxes
- IoT Devices
- IoT Jobs
- token-based agent registration
- heartbeat monitoring
- job polling and result reporting
- automatic device synchronization from external agents

## Repository layout

This repository is intentionally structured as an Odoo addon repository:

- `community_iot_box/`

## Compatibility

- Odoo 17 Community
- Odoo.sh private projects
- On-premise Odoo deployments
- Not compatible with Odoo Online (SaaS), because this addon contains Python
  code.

## Requirements

- A separately deployed IoT Box Community Agent for Linux or Windows.
- The agent is configured by the administrator. This addon never downloads,
  installs or executes agent code.

## Current scope

The module includes:

- backend models and views
- REST endpoints for IoT agents
- test print job creation
- support for autodetected devices reported by the agent

The external IoT agent is not included in this repository.

## Installation

1. Copy `community_iot_box` into your Odoo custom addons path.
2. Update the apps list.
3. Install `IoT Box Community`.
4. Create an IoT Box record and generate its token.
5. Install IoT Box Community Agent on the device host and configure it with:
   - Odoo URL
   - optional database name
   - IoT token
6. Confirm the heartbeat and discovered devices in Odoo, then run a test print.

## Heartbeat monitoring

An Online box becomes Offline when its last heartbeat is **more than 180 seconds**
old (or is missing). This grace period tolerates transient missed heartbeats.
A successful heartbeat (`status: "ok"`) restores Online and clears the offline
warning. Draft boxes remain Draft and reported Error states are preserved.

The `odoo-cups-agent` implementation sends a heartbeat in every polling cycle
(`Agent.poll_once`), with a default two-second pause after successful cycles.
Failed cycles back off up to 60 seconds with ±20% jitter (up to 72 seconds),
and control-plane HTTP calls default to a ten-second timeout. The three-minute
grace period allows transient failed cycles rather than expiring on one missed
heartbeat. Cycle work and CUPS/network delays can lengthen the actual interval.

Administrators can override the grace period in **Settings → Technical →
Parameters → System Parameters** with the key
`community_iot_box.heartbeat_timeout_seconds` and a positive integer in seconds.
Missing, invalid or nonpositive values use 180 seconds. Confirm the deployed
agent's configured heartbeat interval and set the grace period
to allow several missed heartbeats, including normal network/request delays.

The scheduled action **Community IoT: expire stale heartbeats** runs every minute,
independently of print jobs. Box kanban/list/form views show the persisted status
on their next read after this check (normally within 60 seconds after expiration,
provided Odoo's scheduler is running). The dashboard also expires stale heartbeats
when opened or manually refreshed. It reports a snapshot and does not refresh
automatically. The connection test applies the same grace period. The scheduled
check's timing excludes server delays.

Upgrade the installed `community_iot_box` addon to load the new scheduled action.

Regression tests: run `sh scripts/run-tests.sh` in an Odoo 17/PostgreSQL test
environment.

## Agent distribution

The agent is a separate product and release stream distributed by JDA SOLUTIONS.
The Odoo addon remains free and open source for users who prefer to run or
develop their own compatible agent.

## Support

For installation guidance or a reproducible defect in the advertised workflow,
use the support contact published on the Odoo Apps listing.

## License

LGPL-3
