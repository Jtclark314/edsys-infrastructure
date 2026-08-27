# EdCore Automation Node-RED Project

This seed is copied into the persistent `/data` volume and initialized as a
real Git repository on first start. The initial flow contains dependency
status/error paths but no production actuation logic.

Rules for future flows:

1. Keep every dependency's timeout, status, catch, and recovery path explicit.
2. Publish requested actions only to `edsys/v1/automation/request/nodered`.
   Node-RED has no ACL permission to publish final Home Assistant commands.
3. Require an acknowledgment with the same command ID before declaring
   completion.
4. Add a sanitized replay fixture and test in `edsys/test/v1/replay` first.
5. Commit reviewed Project changes before deployment; never commit generated
   credentials or runtime traces.
6. Keep safety-related deterministic automations and final actuation in Home
   Assistant.
