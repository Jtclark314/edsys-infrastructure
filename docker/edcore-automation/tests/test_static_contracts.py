"""Static deployment-contract tests with no Docker daemon or third-party YAML parser."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_STACK = "/srv/edsys/edsys-infrastructure/docker/edcore-automation"
INSTALLED_SOURCE_GUARD = "/usr/local/sbin/edsys-automation-source-guard"


def text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def active_lines(relative: str) -> list[str]:
    return [
        line.strip()
        for line in text(relative).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def listener_block(configuration: str, port: int) -> str:
    match = re.search(
        rf"(?ms)^listener {port}\b.*?(?=^listener \d+\b|\Z)",
        configuration,
    )
    if match is None:
        raise AssertionError(f"missing listener {port}")
    return match.group(0)


def acl_users(relative: str) -> dict[str, list[str]]:
    users: dict[str, list[str]] = {}
    current: str | None = None
    for line in active_lines(relative):
        if line.startswith("user "):
            current = line.removeprefix("user ").strip()
            if current in users:
                raise AssertionError(f"duplicate ACL user {current!r}")
            users[current] = []
        elif line.startswith("topic "):
            if current is None:
                raise AssertionError(f"topic precedes user in {relative}")
            users[current].append(line)
        else:
            raise AssertionError(f"unexpected ACL directive: {line!r}")
    return users


def compose_service(relative: str, service: str) -> str:
    configuration = text(relative)
    services = configuration.split("\nservices:\n", 1)[1]
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:\n.*?(?=^  [a-z0-9-]+:\n|\Z)",
        services,
    )
    if match is None:
        raise AssertionError(f"missing Compose service {service} in {relative}")
    return match.group(0)


def compose_service_networks(relative: str, service: str) -> set[str]:
    """Return explicitly attached Compose networks without a YAML dependency."""

    section = compose_service(relative, service)
    inline = re.search(r"(?m)^    networks: \[([^]]*)\]$", section)
    if inline is not None:
        return {item.strip() for item in inline.group(1).split(",") if item.strip()}

    block = re.search(
        r"(?ms)^    networks:\n(.*?)(?=^    [a-z][a-z0-9_-]*:|\Z)",
        section,
    )
    if block is None:
        return {"default"}
    return set(re.findall(r"(?m)^      ([a-z][a-z0-9-]*):\s*$", block.group(1)))


def internal_compose_networks(relative: str) -> set[str]:
    """Return top-level Compose networks declared internal."""

    definitions = text(relative).split("\nnetworks:\n", 1)[1]
    internal: set[str] = set()
    for match in re.finditer(
        r"(?ms)^  ([a-z][a-z0-9-]*):\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|\Z)",
        definitions,
    ):
        if re.search(r"(?m)^    internal: true$", match.group(2)):
            internal.add(match.group(1))
    return internal


class MosquittoContractTestCase(unittest.TestCase):
    def test_listeners_have_distinct_acl_boundaries_and_global_retention(self) -> None:
        configuration = text("mosquitto/mosquitto.conf")
        self.assertIn("per_listener_settings true", configuration)
        external = listener_block(configuration, 8883)
        internal = listener_block(configuration, 8884)

        common_mtls = (
            "allow_anonymous false",
            "require_certificate true",
            "use_identity_as_username true",
            "cafile /run/secrets/automation_ca_cert",
            "certfile /run/secrets/mosquitto_server_cert",
            "keyfile /run/secrets/mosquitto_server_key",
            "tls_version tlsv1.2",
        )
        for directive in common_mtls:
            with self.subTest(listener=8883, directive=directive):
                self.assertIn(directive, external)
            with self.subTest(listener=8884, directive=directive):
                self.assertIn(directive, internal)

        self.assertIn("acl_file /mosquitto/config/aclfile\n", external)
        self.assertIn("acl_file /mosquitto/config/aclfile-internal", internal)
        # Mosquitto defines retain_available globally, not per listener. Keep
        # legitimate HA/Frigate discovery while the exclusive publishers below
        # hard-code command non-retention.
        self.assertEqual(active_lines("mosquitto/mosquitto.conf").count("retain_available true"), 1)
        self.assertNotIn("retain_available false", configuration)
        self.assertLess(configuration.index("retain_available true"), configuration.index("listener 8883"))

    def test_external_acl_has_no_request_or_command_writer(self) -> None:
        users = acl_users("mosquitto/aclfile")
        self.assertIn("homeassistant", users)
        self.assertIn("frigate", users)
        self.assertIn("event-replay", users)
        for user, rules in users.items():
            for rule in rules:
                with self.subTest(user=user, rule=rule):
                    if rule.startswith(("topic write ", "topic readwrite ")):
                        self.assertNotIn("edsys/v1/automation/request", rule)
                        self.assertNotIn("edsys/v1/command", rule)

    def test_synthetic_edge_identity_is_write_only_and_cannot_control(self) -> None:
        users = acl_users("mosquitto/aclfile")
        self.assertEqual(
            users["edsys-edge-livingroom"],
            [
                "topic write edsys/v1/telemetry/environment/edge-livingroom/#",
                "topic write edsys/v1/state/edge-livingroom/#",
                "topic write edsys/v1/availability/edge-livingroom/#",
            ],
        )
        self.assertFalse(
            any(
                "request" in rule or "command" in rule or rule.startswith("topic read")
                for rule in users["edsys-edge-livingroom"]
            )
        )

    def test_command_audit_identity_is_read_only_and_used_for_restore(self) -> None:
        users = acl_users("mosquitto/aclfile")
        self.assertEqual(users["command-audit"], ["topic read edsys/v1/command/ha/#"])

        restore = text("scripts/restore-test.sh")
        self.assertIn("pki/clients/command-audit.crt", restore)
        self.assertIn("pki/clients/command-audit.key", restore)
        self.assertIn("--cert /run/secrets/command_audit_client_cert", restore)
        self.assertIn("--key /run/secrets/command_audit_client_key", restore)
        self.assertNotIn("pki/clients/homeassistant.crt", restore)
        self.assertNotIn("pki/clients/homeassistant.key", restore)

    def test_internal_acl_is_the_only_request_and_command_write_path(self) -> None:
        users = acl_users("mosquitto/aclfile-internal")
        self.assertEqual(set(users), {"nodered", "automation-runtime"})
        self.assertIn(
            "topic write edsys/v1/automation/request/nodered",
            users["nodered"],
        )
        self.assertFalse(any("command/ha" in rule for rule in users["nodered"]))
        self.assertIn(
            "topic read edsys/v1/automation/request/#",
            users["automation-runtime"],
        )
        self.assertIn(
            "topic write edsys/v1/command/ha/#",
            users["automation-runtime"],
        )
        writers = [
            user
            for user, rules in users.items()
            if any(rule == "topic write edsys/v1/command/ha/#" for rule in rules)
        ]
        self.assertEqual(writers, ["automation-runtime"])


class NodeRedContractTestCase(unittest.TestCase):
    def test_editor_and_admin_api_are_tls_authenticated_and_bounded(self) -> None:
        settings = text("node-red/settings.js")
        for contract in (
            'uiHost: "0.0.0.0"',
            "https:",
            "adminAuth:",
            "httpNodeAuth:",
            "httpStaticAuth:",
            "credentialSecret: readSecret(\"/run/secrets/node_red_credential_secret\")",
            "enabled: true",
            'workflow: {mode: "manual"}',
            "autoInstall: false",
            "allowUpload: false",
            "functionTimeout: 10",
            "telemetry: {enabled: false}",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, settings)

    def test_project_is_git_backed_and_project_secret_must_match(self) -> None:
        entrypoint = text("node-red/entrypoint.sh")
        self.assertIn('git -C "$project_dir" init --initial-branch=main', entrypoint)
        self.assertIn('/run/secrets/node_red_credential_secret', entrypoint)
        self.assertIn('configured !== secret', entrypoint)
        self.assertIn('Active Project credential encryption does not match', entrypoint)
        self.assertIn('fs.chmodSync(path, 0o600)', entrypoint)
        self.assertNotIn('credentialSecret":false', entrypoint)

        seed_credentials = text("node-red/project-seed/flows_cred.json").strip()
        self.assertEqual(seed_credentials, "{}")
        project_ignore = text("node-red/project-seed/.gitignore")
        self.assertNotIn("flows_cred.json", project_ignore)

    def test_seed_uses_only_internal_mqtt_and_has_no_actuator_output(self) -> None:
        flows = json.loads(text("node-red/project-seed/flows.json"))
        brokers = [node for node in flows if node.get("type") == "mqtt-broker"]
        self.assertEqual(len(brokers), 1)
        self.assertEqual(str(brokers[0].get("port")), "8884")
        self.assertTrue(brokers[0].get("usetls"))

        mqtt_outputs = [node for node in flows if node.get("type") == "mqtt out"]
        for node in mqtt_outputs:
            with self.subTest(node=node.get("id")):
                self.assertEqual(str(node.get("retain")).lower(), "false")
        serialized = json.dumps(flows)
        self.assertNotIn("edsys/v1/command/ha/", serialized)

        types = {node.get("type") for node in flows}
        self.assertIn("status", types)
        self.assertIn("catch", types)

    def test_localized_connected_status_and_managed_release_are_exact(self) -> None:
        self.assertEqual(text("node-red/project-seed/.edsys-release"), "1.0.2\n")
        flows = json.loads(text("node-red/project-seed/flows.json"))
        functions = {node.get("id"): node.get("func", "") for node in flows if node.get("type") == "function"}
        localized_test = "text === 'connected' || text.endsWith('.status.connected')"
        self.assertIn(localized_test, functions["normalise-mqtt-status"])
        self.assertIn(localized_test, functions["mqtt-health-state"])
        self.assertIn(
            "msg.payload = connected ? 'connected\\n' : 'not-connected\\n'",
            functions["mqtt-health-state"],
        )
        self.assertIn(
            '!== "connected\\n"',
            text("node-red/healthcheck.js"),
        )

        entrypoint = text("node-red/entrypoint.sh")
        self.assertIn("for managed in .edsys-release", entrypoint)
        self.assertIn('release=$(cat /opt/edsys/project-seed/.edsys-release)', entrypoint)
        self.assertIn('Update reviewed EdCore automation project to $release', entrypoint)
        deploy = text("scripts/deploy.sh")
        self.assertIn("/data/projects/edcore-automation/.edsys-release", deploy)
        self.assertIn("1.0.2", deploy)
        verify = text("scripts/verify.sh")
        self.assertIn("/data/projects/edcore-automation/.edsys-release", verify)
        self.assertIn("1.0.2", verify)


class ComposeContractTestCase(unittest.TestCase):
    def test_expected_services_and_tool_profile_are_present(self) -> None:
        compose = text("compose.yaml")
        services_section = compose.split("\nservices:\n", 1)[1].split("\nsecrets:\n", 1)[0]
        services = set(re.findall(r"(?m)^  ([a-z0-9-]+):\n", services_section))
        self.assertEqual(
            services,
            {"mosquitto", "influxdb", "automation-runtime", "node-red", "telegraf", "event-harness"},
        )
        event_section = services_section.split("\n  event-harness:\n", 1)[1]
        self.assertIn('profiles: ["tools"]', event_section)
        self.assertIn('entrypoint: ["python", "-m", "event_harness.cli"]', event_section)
        self.assertNotIn("grafana:", services_section)
        self.assertNotIn("appdaemon:", services_section)

    def test_only_exact_lan_ports_are_published_and_8884_is_internal(self) -> None:
        compose = text("compose.yaml")
        published = re.findall(r'(?m)^\s+- "([^\"]+:[0-9]+:[0-9]+/tcp)"$', compose)
        self.assertEqual(
            set(published),
            {
                "${LAN_BIND_ADDRESS:?set LAN_BIND_ADDRESS}:8883:8883/tcp",
                "${LAN_BIND_ADDRESS:?set LAN_BIND_ADDRESS}:1880:1880/tcp",
                "${LAN_BIND_ADDRESS:?set LAN_BIND_ADDRESS}:8086:8086/tcp",
            },
        )
        self.assertFalse(any(":8884:" in publication for publication in published))
        self.assertFalse(any(publication.startswith("0.0.0.0:") for publication in published))

    def test_images_state_and_secrets_are_explicit(self) -> None:
        compose = text("compose.yaml")
        official_images = re.findall(r"(?m)^\s+image: (docker\.io/\S+)$", compose)
        self.assertTrue(official_images)
        for image in official_images:
            with self.subTest(image=image):
                self.assertRegex(image, r":[^@\s]+@sha256:[0-9a-f]{64}$")
        self.assertIn("restart: unless-stopped", compose)
        self.assertIn("no-new-privileges:true", compose)
        self.assertIn("cap_drop:\n    - ALL", compose)
        self.assertIn("AUTOMATION_POLICY_PATH: /app/config/policy.json", compose)
        self.assertIn("AUTOMATION_STATE_DB: /var/lib/automation-runtime/seen.sqlite3", compose)
        self.assertIn("/etc/edsys-secrets/edcore-automation/", compose)
        self.assertNotRegex(compose, r"(?im)^\s*DOCKER_INFLUXDB_INIT_PASSWORD:\s*\S+")
        self.assertNotRegex(compose, r"(?im)^\s*DOCKER_INFLUXDB_INIT_ADMIN_TOKEN:\s*\S+")
        self.assertNotRegex(compose, r"(?im)^\s*(?:PASSWORD|TOKEN|API_KEY|SECRET):\s*\S+")

    def test_four_plane_topology_and_all_service_resources_are_bounded(self) -> None:
        compose = text("compose.yaml")
        networks = compose.split("\nnetworks:\n", 1)[1]
        self.assertEqual(
            set(re.findall(r"(?m)^  ([a-z]+):\n", networks)),
            {"broker", "data", "ingress", "egress"},
        )
        self.assertRegex(
            networks,
            r"(?ms)^  broker:\n    name: edsys-edcore-automation-broker\n    internal: true$",
        )
        self.assertRegex(
            networks,
            r"(?ms)^  data:\n    name: edsys-edcore-automation-data\n    internal: true$",
        )
        self.assertRegex(
            networks,
            r"(?ms)^  ingress:\n"
            r"    name: edsys-edcore-automation-ingress\n"
            r"    driver: bridge\n"
            r"    enable_ipv6: false\n"
            r"    driver_opts:\n"
            r"      com\.docker\.network\.bridge\.name: br-ed-ingress\n"
            r"      com\.docker\.network\.bridge\.enable_icc: \"false\"\n"
            r"    ipam:\n"
            r"      config:\n"
            r"        - subnet: 172\.31\.82\.16/29\n"
            r"          gateway: 172\.31\.82\.17(?:\n|\Z)",
        )
        self.assertRegex(
            networks,
            r"(?ms)^  egress:\n"
            r"    name: edsys-edcore-automation-egress\n"
            r"    driver: bridge\n"
            r"    driver_opts:\n"
            r"      com\.docker\.network\.bridge\.name: br-edsys-egress\n"
            r"    ipam:\n"
            r"      config:\n"
            r"        - subnet: 172\.31\.82\.0/28\n"
            r"          gateway: 172\.31\.82\.1(?:\n|\Z)",
        )
        expected_networks = {
            "mosquitto": (
                "networks:\n"
                "      broker:\n"
                "      ingress:\n"
                "        ipv4_address: 172.31.82.18"
            ),
            "influxdb": (
                "networks:\n"
                "      data:\n"
                "      ingress:\n"
                "        ipv4_address: 172.31.82.19"
            ),
            "automation-runtime": "networks: [broker]",
            "node-red": (
                "networks:\n"
                "      broker:\n"
                "      data:\n"
                "      egress:\n"
                "        ipv4_address: 172.31.82.2"
            ),
            "telegraf": "networks: [broker, data]",
            "event-harness": "networks: [broker]",
        }
        for service, network_contract in expected_networks.items():
            section = compose_service("compose.yaml", service)
            with self.subTest(service=service):
                self.assertIn(network_contract, section)
                for resource in ("pids_limit", "mem_limit", "mem_reservation", "cpus"):
                    self.assertRegex(section, rf"(?m)^    {resource}: [^\s]+$")
                if service != "node-red":
                    self.assertNotIn("egress", section)
                if service not in {"mosquitto", "influxdb"}:
                    self.assertNotIn("ingress", section)
        self.assertIn("ipv4_address: 172.31.82.2", compose_service("compose.yaml", "node-red"))
        self.assertEqual(compose_service_networks("compose.yaml", "mosquitto"), {"broker", "ingress"})
        self.assertEqual(compose_service_networks("compose.yaml", "influxdb"), {"data", "ingress"})
        self.assertEqual(internal_compose_networks("compose.yaml"), {"broker", "data"})

    def test_fixed_bridge_names_are_safe_ascii_and_fit_linux_ifnamsiz(self) -> None:
        compose = text("compose.yaml")
        bridge_names = re.findall(
            r"(?m)^\s+com\.docker\.network\.bridge\.name: (\S+)$",
            compose,
        )
        self.assertEqual(bridge_names, ["br-ed-ingress", "br-edsys-egress"])
        self.assertEqual(
            {name: len(name.encode("ascii")) for name in bridge_names},
            {"br-ed-ingress": 13, "br-edsys-egress": 15},
        )
        for bridge_name in bridge_names:
            with self.subTest(bridge_name=bridge_name):
                encoded = bridge_name.encode("ascii")
                self.assertRegex(bridge_name, r"\A[A-Za-z0-9_.-]+\Z")
                self.assertLessEqual(len(encoded), 15)

    def test_live_ubuntu_nft_fixture_is_an_exact_deterministic_render(self) -> None:
        template = text("firewall/edsys-automation-firewall.nft.in")
        fixture = text("tests/fixtures/edsys-automation-firewall-ubuntu.nft")
        production_table = "table inet edsys_automation_filter {"
        fixture_table = "table inet edsys_automation_nft_parse_fixture {"

        self.assertEqual(template.count(production_table), 1)
        self.assertEqual(template.count("@LAN_IFACE@"), 5)
        expected = template.replace(production_table, fixture_table, 1).replace(
            "@LAN_IFACE@",
            "ens18",
        )
        self.assertEqual(fixture, expected)
        self.assertEqual(fixture.count(fixture_table), 1)
        self.assertNotIn(production_table, fixture)
        self.assertNotIn("@LAN_IFACE@", fixture)
        self.assertRegex("ens18", r"\A[A-Za-z0-9_.-]+\Z")
        self.assertLessEqual(len("ens18".encode("ascii")), 15)

    def test_a_published_service_must_not_be_internal_only(self) -> None:
        compose = text("compose.yaml")
        services = compose.split("\nservices:\n", 1)[1].split("\nsecrets:\n", 1)[0]
        service_names = set(re.findall(r"(?m)^  ([a-z0-9-]+):\n", services))
        internal = internal_compose_networks("compose.yaml")
        published = {
            service
            for service in service_names
            if re.search(r"(?m)^    ports:$", compose_service("compose.yaml", service))
        }
        self.assertEqual(published, {"mosquitto", "influxdb", "node-red"})
        for service in sorted(published):
            with self.subTest(service=service):
                attached = compose_service_networks("compose.yaml", service)
                self.assertTrue(
                    attached - internal,
                    f"published service {service} is attached only to internal networks: {attached}",
                )

    def test_influx_bootstrap_secrets_exist_only_in_one_time_override(self) -> None:
        steady = compose_service("compose.yaml", "influxdb")
        bootstrap = compose_service("compose.bootstrap.yaml", "influxdb")
        for tls_secret in ("influxdb_tls_cert", "influxdb_tls_key"):
            self.assertIn(f"      - {tls_secret}", steady)
        for bootstrap_secret in ("influxdb_admin_password", "influxdb_admin_token"):
            with self.subTest(bootstrap_secret=bootstrap_secret):
                self.assertNotIn(bootstrap_secret, steady)
                self.assertIn(f"      - {bootstrap_secret}", bootstrap)
        self.assertNotIn("DOCKER_INFLUXDB_INIT_", steady)
        self.assertIn("DOCKER_INFLUXDB_INIT_MODE: setup", bootstrap)
        self.assertIn("DOCKER_INFLUXDB_INIT_PASSWORD_FILE", bootstrap)
        self.assertIn("DOCKER_INFLUXDB_INIT_ADMIN_TOKEN_FILE", bootstrap)

    def test_telegraf_uses_numeric_json_without_producer_tags_and_bounded_aggregation(self) -> None:
        configuration = text("telegraf/telegraf.conf")
        self.assertEqual(configuration.count('keepalive = "60s"'), 2)
        self.assertEqual(configuration.count('data_format = "json_v2"'), 2)
        self.assertEqual(configuration.count('path = "value"'), 2)
        self.assertEqual(configuration.count('rename = "value"'), 2)
        self.assertEqual(configuration.count('type = "float"'), 2)
        for tag_directive in ("tag_keys", "topic_tag", "json_name_key", "json_v2.tag"):
            with self.subTest(forbidden_tag_directive=tag_directive):
                self.assertNotIn(tag_directive, configuration)
        self.assertEqual(configuration.count("[[aggregators.basicstats]]"), 1)
        for contract in (
            'period = "10s"',
            'delay = "2s"',
            'grace = "2s"',
            "drop_original = true",
            'namepass = ["selected_telemetry", "highrate_telemetry"]',
            'fieldinclude = ["value"]',
            'stats = ["count", "min", "max", "mean", "stdev"]',
        ):
            with self.subTest(basicstats_contract=contract):
                self.assertIn(contract, configuration)

    def test_runtime_uses_internal_listener_and_event_harness_uses_external_listener(self) -> None:
        compose = text("compose.yaml")
        runtime_section = compose.split("\n  automation-runtime:\n", 1)[1].split("\n  node-red:\n", 1)[0]
        harness_section = compose.split("\n  event-harness:\n", 1)[1].split("\nsecrets:\n", 1)[0]
        self.assertIn('MQTT_PORT: "8884"', runtime_section)
        self.assertIn('MQTT_PORT: "8883"', harness_section)
        self.assertIn("read_only: true", runtime_section)
        self.assertIn("read_only: true", harness_section)

    def test_exclusive_runtime_rejects_retained_requests_and_never_retains_commands(self) -> None:
        service = text("runtime/src/automation_runtime/service.py")
        ledger = text("runtime/src/automation_runtime/ledger.py")
        self.assertIn("mqtt.SubscribeOptions(", service)
        self.assertIn("retainAsPublished=True", service)
        self.assertIn("bool(message.retain)", service)
        self.assertIn("if retained:", service)
        self.assertIn('"retained_request"', service)
        command_publish = re.search(
            r"self\.client\.publish\(\s*command\.output_topic,.*?\n\s*\)",
            service,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(command_publish)
        self.assertIn("retain=False", command_publish.group(0))
        self.assertIn("properties=properties", command_publish.group(0))
        # QoS 1 wait timeout is an unknown outcome: the broker may already
        # have the command. Preserve the claimed ID so a retry cannot duplicate
        # a possibly in-flight actuation.
        self.assertIn('"publish_outcome_unknown"', service)
        self.assertNotIn(".release(", service)
        self.assertNotIn("def release", ledger)


class OperatorAssetContractTestCase(unittest.TestCase):
    def test_operator_docs_require_transfer_normalization_before_root_execution(self) -> None:
        readme = text("README.md")
        migration = text("MIGRATION.md")
        for document_name, document in (("README.md", readme), ("MIGRATION.md", migration)):
            for contract in (
                CANONICAL_STACK,
                "root:root 0755",
                "root:root 0644",
                "root:root 0640",
                "1883:0 0640",
                "mosquitto/mosquitto.conf",
                "mosquitto/aclfile",
                "mosquitto/aclfile-internal",
                INSTALLED_SOURCE_GUARD,
                "--runtime",
                "--coherent",
            ):
                with self.subTest(document=document_name, contract=contract):
                    self.assertIn(contract, document)
        for unsafe_kind in ("symlink", "special file", "hardlink"):
            with self.subTest(unsafe_kind=unsafe_kind):
                self.assertIn(unsafe_kind, readme)

        self.assertLess(
            readme.index("Transfer the reviewed"),
            readme.index("normalize ownership and modes"),
        )
        self.assertLess(
            readme.index("normalize ownership and modes"),
            readme.index('sudo "$STACK/scripts/source-guard.sh" --transfer'),
        )
        self.assertLess(
            migration.index("Transfer the reviewed stack as data"),
            migration.index("Run `sudo scripts/source-guard.sh --transfer`"),
        )

    def test_operator_docs_cover_policy_isolation_escrow_and_acceptance_gates(self) -> None:
        readme = text("README.md")
        migration = text("MIGRATION.md")
        common_contracts = (
            '"allowed": []',
            "required",
            "properties",
            "NaN",
            "entity_id",
            "compose.bootstrap.yaml",
            "/etc/edsys-secrets/edcore-automation-escrow/identity.txt",
            "/usr/local/sbin/edsys-automation-verify-secret-escrow",
            "/usr/local/libexec/edsys-automation-secret-escrow-archive.py",
            "scripts/secret_escrow_archive.py",
            "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
            "PYTHONHOME",
            "PYTHONPATH",
            "python3 -I -B",
            "duplicate normalized paths",
            "PAX",
            "32 MiB",
            "512 members",
            "4 MiB",
            "16 MiB",
            "/etc/edsys-escrow/edcore-automation-accepted.json",
            "record-client-delivery.sh homeassistant --accepted",
            "record-client-delivery.sh frigate --accepted",
            "verify-edge-ingestion.sh --accept",
            "/etc/edsys-escrow/client-disposition/edsys-edge-livingroom-ingestion.json",
            "edsys.edcore-automation.synthetic-ingestion-acceptance.v1",
            "edsys/v1/telemetry/environment/edge-livingroom/synthetic",
            "record-client-disposition.sh edsys-edge-livingroom --unused",
            "/etc/edsys-escrow/online-keys-finalized.json",
            "edsys.edcore-automation.online-key-finalization.v1",
            "command-audit",
            "/etc/edsys-secrets/edcore-automation/healthchecks/backup.env",
            "/etc/edsys-secrets/edcore-automation/healthchecks/restore-test.env",
            "root:root 0600",
            "1.0.2",
            "localized",
            "four",
            "edsys-edcore-automation-ingress",
            "br-ed-ingress",
            "13 ASCII bytes",
            "15 ASCII bytes",
            "172.31.82.16/29",
            "172.31.82.17",
            "172.31.82.18",
            "172.31.82.19",
            "com.docker.network.bridge.enable_icc=false",
            "enable_ipv6: false",
            "EnableIPv6 == false",
            "firewall/edsys-automation-firewall.nft.in",
            "tests/fixtures/edsys-automation-firewall-ubuntu.nft",
            "tests/fixtures/docker29-ingress-network-inspect.json",
            "sudo nft -c -f tests/fixtures/edsys-automation-firewall-ubuntu.nft",
            "check-only",
            "Ubuntu 24.04",
            "fixture table remained absent afterward",
            "scripts/install-firewall.sh --apply",
            '"IPRange": ""',
            "null/nonempty `IPRange`",
            "extra IPAM key",
            "second Config item",
            "only the expected Mosquitto and InfluxDB endpoints",
            "No delete-or-reuse cleanup",
            'oifname "br-ed-ingress" counter drop',
            "userland-proxy",
            "allow-direct-routing",
            "non-`192.168.50.50`",
            "direct container-IP",
            "unpublished-port",
            "NetworkSettings.Ports",
            "br-edsys-egress",
            "172.31.82.0/28",
            "172.31.82.2",
            "192.168.50.75:8123",
            "1.1.1.1:443",
            "PermitRootLogin no",
            "AllowUsers jeremy edsys-backup",
            "tests/fixtures/influxdb-2.8.0-amd64-config-env-names.json",
            "DOCKER_INFLUXDB_INIT_CLI_CONFIG_NAME",
            "DOCKER_INFLUXDB_INIT_MODE",
            "DOCKER_INFLUXDB_INIT_USERNAME",
            "DOCKER_INFLUXDB_INIT_PASSWORD_FILE",
            "DOCKER_INFLUXDB_INIT_ADMIN_TOKEN_FILE",
            "DOCKER_INFLUXDB_INIT_ORG",
            "DOCKER_INFLUXDB_INIT_BUCKET",
            "DOCKER_INFLUXDB_INIT_RETENTION",
            "zero-length/default client ID",
            "23 bytes",
            "distinct IDs",
            "broker diagnostic",
            "zero-ID protocol rejection",
            "missing-client-certificate",
            "nanosecond log",
            "accepted-session",
            "publisher exit status",
            "PUBACK (Mid: 1, RC:135)",
            "Warning: Publish 1 failed: Not authorized.",
            "concurrent authenticated zero-delivery audit",
            "topic suffix",
            "payload `source`",
            "independent deterministic SHA-256",
            "no raw edge identity",
        )
        for document_name, document in (("README.md", readme), ("MIGRATION.md", migration)):
            for contract in common_contracts:
                with self.subTest(document=document_name, contract=contract):
                    self.assertIn(contract, document)

        for readme_contract in (
            "edsys-edcore-automation-broker",
            "edsys-edcore-automation-data",
            "edsys-edcore-automation-ingress",
            "edsys-edcore-automation-egress",
            "json_v2",
            "60-second MQTT keepalives",
            "CPU, memory/reservation, and PID limits",
            "curl` through stdin configuration",
            "invalid atomic firewall",
            "secret-escrow/ACCEPTANCE.json",
            "custody-evidence/",
        ):
            with self.subTest(readme_contract=readme_contract):
                self.assertIn(readme_contract, readme)

        self.assertLess(
            migration.index("Use `sudo scripts/deploy.sh` for first deployment"),
            migration.index("run\n  `sudo scripts/verify-edge-ingestion.sh --accept`"),
        )
        self.assertLess(
            migration.index("run\n  `sudo scripts/verify-edge-ingestion.sh --accept`"),
            migration.index("run `sudo scripts/create-secret-escrow.sh --create`"),
        )
        self.assertLess(
            migration.index("run `sudo scripts/create-secret-escrow.sh --create`"),
            migration.index("Run `sudo scripts/finalize-online-keys.sh --apply`"),
        )
        self.assertLess(
            migration.index("Run `sudo scripts/finalize-online-keys.sh --apply`"),
            migration.index("Run `sudo scripts/verify.sh`"),
        )
        self.assertNotIn("existing Grafana", readme)
        self.assertNotIn("existing Grafana", migration)
        self.assertIn("No active general Grafana", readme)
        self.assertIn("No active general Grafana", migration)
        self.assertIn("to be confirmed", readme)

    def test_source_guard_enforces_exact_installed_tree_states(self) -> None:
        guard = text("scripts/source-guard.sh")

        self.assertIn(f"readonly expected_stack={CANONICAL_STACK}", guard)
        self.assertIn("--transfer|--runtime|--coherent", guard)
        self.assertIn('[[ ${EUID} -eq 0 ]]', guard)
        self.assertIn('[[ $(readlink -e -- "$expected_stack") == "$expected_stack" ]]', guard)
        self.assertIn('require_safe_chain "$expected_stack"', guard)
        self.assertIn('[[ $owner == 0:0 ]]', guard)
        self.assertIn('(( (8#$mode & 8#022) == 0 ))', guard)

        # Only directories and ordinary, singly linked files may be consumed
        # by root. This rejects symlinks, devices/FIFOs/sockets, and hardlinks
        # before mode-specific validation.
        self.assertIn(r'\! -type d \! -type f -print -quit', guard)
        self.assertIn('-type f -links +1 -print -quit', guard)
        self.assertLess(
            guard.index(r'\! -type d \! -type f -print -quit'),
            guard.index('find "$expected_stack" -xdev -type f -print0'),
        )

        self.assertIn('require_triplet "$directory" 0:0:755', guard)
        self.assertIn("expected=0:0:644", guard)
        self.assertIn(".env)\n      expected=0:0:640", guard)
        self.assertIn("scripts/*.sh|node-red/entrypoint.sh)\n      expected=0:0:755", guard)
        self.assertIn(
            "mosquitto/mosquitto.conf|mosquitto/aclfile|mosquitto/aclfile-internal)",
            guard,
        )
        self.assertIn("expected=1883:0:640", guard)
        self.assertIn("if [[ $phase == --runtime ]]", guard)

        # Coherent is intentionally only a cold-boot bridge: it chooses one
        # complete exact Mosquitto state and still checks installed commands.
        self.assertIn("if [[ $phase == --coherent ]]", guard)
        self.assertIn("phase=--transfer", guard)
        self.assertIn("phase=--runtime", guard)
        self.assertIn(
            "if [[ $phase == --runtime || $requested_phase == --coherent ]]",
            guard,
        )

    def test_every_tracked_executable_is_covered_by_the_0755_contract(self) -> None:
        guard = text("scripts/source-guard.sh")
        self.assertIn("scripts/*.sh|node-red/entrypoint.sh)", guard)

        shebang_sources = {
            path.relative_to(ROOT).as_posix()
            for path in ROOT.rglob("*")
            if path.is_file()
            and path.read_bytes().startswith(b"#!")
        }
        expected = {
            "node-red/entrypoint.sh",
            *(f"scripts/{path.name}" for path in (ROOT / "scripts").glob("*.sh")),
        }
        self.assertEqual(shebang_sources, expected)
        self.assertIn("scripts/verify-edge-ingestion.sh", shebang_sources)
        self.assertIn("scripts/install-firewall.sh", shebang_sources)
        for relative in sorted(shebang_sources):
            with self.subTest(executable=relative):
                self.assertTrue((ROOT / relative).stat().st_mode & 0o111)

    def test_privileged_entrypoints_install_and_run_the_source_guard_first(self) -> None:
        bootstrap = text("scripts/bootstrap-guest.sh")
        deploy = text("scripts/deploy.sh")
        firewall_installer = text("scripts/install-firewall.sh")
        verify = text("scripts/verify.sh")

        install_contract = re.compile(
            rf"install -o root -g root -m 0755.{{0,180}}"
            rf"source-guard\.sh.{{0,180}}{re.escape(INSTALLED_SOURCE_GUARD)}",
            flags=re.DOTALL,
        )
        self.assertRegex(bootstrap, install_contract)
        self.assertRegex(deploy, install_contract)

        bootstrap_guard = '"$stack_dir/scripts/source-guard.sh" "$guard_phase"'
        deploy_guard = '"$stack_dir/scripts/source-guard.sh" "$guard_phase"'
        verify_guard = f"{INSTALLED_SOURCE_GUARD} --runtime"
        self.assertIn(bootstrap_guard, bootstrap)
        self.assertIn(deploy_guard, deploy)
        self.assertIn(verify_guard, verify)
        self.assertLess(bootstrap.index(bootstrap_guard), bootstrap.index("apt-get update"))
        self.assertLess(deploy.index(deploy_guard), deploy.index("source .env"))
        self.assertLess(verify.index(verify_guard), verify.index("source .env"))
        installer_guard = '"$stack_dir/scripts/source-guard.sh" "$guard_phase"'
        self.assertIn(installer_guard, firewall_installer)
        self.assertLess(
            firewall_installer.index(installer_guard),
            firewall_installer.index("candidate=$(mktemp"),
        )

        # Runtime ownership is rechecked after Mosquitto's only three source
        # exceptions are applied and before Docker/systemd can consume them.
        runtime_guard = f"{INSTALLED_SOURCE_GUARD} --runtime"
        self.assertLess(deploy.index("chown 1883:0 mosquitto/"), deploy.index(runtime_guard))
        self.assertLess(deploy.index(runtime_guard), deploy.index("docker compose"))

    def test_first_deploy_bootstraps_influx_once_then_recreates_steady_state(self) -> None:
        deploy = text("scripts/deploy.sh")
        steady_start = "docker compose up -d --remove-orphans mosquitto influxdb"
        setup_api = "https://edcore-automation.edsys.local:8086/api/v2/setup"
        bootstrap_start = (
            "docker compose -f compose.yaml -f compose.bootstrap.yaml "
            "up -d --force-recreate influxdb"
        )
        steady_recreate = "docker compose up -d --force-recreate influxdb"
        full_start = "docker compose up -d --build --remove-orphans"
        self.assertIn("docker compose -f compose.yaml -f compose.bootstrap.yaml config --quiet", deploy)
        self.assertIn(steady_start, deploy)
        self.assertIn(setup_api, deploy)
        self.assertIn("if [[ $setup_allowed == true ]]", deploy)
        self.assertIn(bootstrap_start, deploy)
        self.assertIn("create_scoped_token telegraf write", deploy)
        self.assertIn("create_scoped_token grafana read", deploy)
        self.assertIn(steady_recreate, deploy)
        self.assertIn("influxdb_admin_(password|token)", deploy)
        self.assertIn("for forbidden_influx_env_name in", deploy)
        self.assertIn(full_start, deploy)
        self.assertLess(deploy.index(steady_start), deploy.index(setup_api))
        self.assertLess(deploy.index(setup_api), deploy.index(bootstrap_start))
        self.assertLess(deploy.index(bootstrap_start), deploy.index("create_scoped_token telegraf write"))
        self.assertLess(deploy.index("create_scoped_token grafana read"), deploy.index(steady_recreate))
        self.assertLess(deploy.index(steady_recreate), deploy.index("influxdb_admin_(password|token)"))
        self.assertLess(deploy.index("for forbidden_influx_env_name in"), deploy.index(full_start))
        self.assertIn("node_red_project_encryption=passed", deploy)
        self.assertIn("event-harness self-test", deploy)
        self.assertIn("systemctl enable edsys-automation-compose.service", deploy)
        self.assertNotIn("set -x", deploy)

        port_gate = re.search(
            r"(?ms)^assert_published_port\(\) \{\n.*?^\}$",
            deploy,
        )
        self.assertIsNotNone(port_gate)
        for effective_contract in (
            '.[0].HostConfig.PortBindings[$key] == [{"HostIp": $host, "HostPort": $port}]',
            '.[0].NetworkSettings.Ports[$key] == [{"HostIp": $host, "HostPort": $port}]',
            'docker compose port "$service" "$port"',
        ):
            self.assertIn(effective_contract, port_gate.group(0))

        initial_start = deploy.index(steady_start)
        initial_mqtt_gate = deploy.index("assert_published_port mosquitto 8883", initial_start)
        initial_influx_gate = deploy.index("assert_published_port influxdb 8086", initial_start)
        setup_position = deploy.index(setup_api)
        self.assertLess(initial_start, initial_mqtt_gate)
        self.assertLess(initial_mqtt_gate, setup_position)
        self.assertLess(initial_influx_gate, setup_position)

        bootstrap_position = deploy.index(bootstrap_start)
        bootstrap_wait = deploy.index("wait_healthy influxdb 180", bootstrap_position)
        bootstrap_gate = deploy.index("assert_published_port influxdb 8086", bootstrap_wait)
        self.assertLess(bootstrap_position, bootstrap_wait)
        self.assertLess(bootstrap_wait, bootstrap_gate)
        self.assertLess(bootstrap_gate, deploy.index("create_scoped_token telegraf write"))

        steady_position = deploy.index(steady_recreate, bootstrap_position)
        steady_wait = deploy.index("wait_healthy influxdb 180", steady_position)
        steady_gate = deploy.index("assert_published_port influxdb 8086", steady_wait)
        self.assertLess(steady_position, steady_wait)
        self.assertLess(steady_wait, steady_gate)
        self.assertLess(steady_gate, deploy.index("influxdb_admin_(password|token)"))

        full_position = deploy.index(full_start)
        for contract in (
            "assert_published_port mosquitto 8883",
            "assert_published_port influxdb 8086",
            "assert_published_port node-red 1880",
        ):
            self.assertGreater(deploy.index(contract, full_position), full_position)
        self.assertEqual(deploy.count("assert_published_port influxdb 8086"), 4)

    def test_influx_setup_parser_is_boolean_total_and_false_path_is_idempotent(self) -> None:
        deploy = text("scripts/deploy.sh")
        parser_match = re.search(
            r"(?ms)^setup_allowed=\$\(curl .*?\| jq -ers '\n"
            r"(?P<program>.*?)\n  '\)$",
            deploy,
        )
        self.assertIsNotNone(parser_match)
        program = parser_match.group("program")
        exact_program = (
            "    if length == 1\n"
            '       and (.[0] | type) == "object"\n'
            '       and (.[0].allowed | type) == "boolean"\n'
            "    then (.[0].allowed | tostring)\n"
            '    else error("InfluxDB setup response must contain one boolean allowed field")\n'
            "    end"
        )
        self.assertEqual(program, exact_program)
        self.assertNotIn("jq -er '.allowed | select", deploy)

        def parse(payload: bytes) -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                ["jq", "-ers", program],
                input=payload,
                capture_output=True,
                check=False,
            )

        for payload, expected in (
            (b'{"allowed":true}', b"true\n"),
            (b'{"allowed":false}', b"false\n"),
        ):
            with self.subTest(accepted=payload):
                result = parse(payload)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(result.stdout, expected)
                self.assertEqual(result.stderr, b"")

        rejected = {
            "empty": b"",
            "missing": b"{}",
            "null": b'{"allowed":null}',
            "string": b'{"allowed":"false"}',
            "number": b'{"allowed":0}',
            "allowed array": b'{"allowed":[]}',
            "top-level array": b'[{"allowed":true}]',
            "top-level boolean": b"true",
            "top-level number": b"1",
            "malformed": b'{"allowed":',
            "multiple JSON values": b'{"allowed":true}\n{"allowed":false}\n',
        }
        for label, payload in rejected.items():
            with self.subTest(rejected=label):
                result = parse(payload)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")

        branch_match = re.search(
            r"(?ms)^if \[\[ \$setup_allowed == true \]\]; then\n.*?^fi$",
            deploy,
        )
        self.assertIsNotNone(branch_match)
        branch = branch_match.group(0)
        self.assertEqual(branch.count("compose.bootstrap.yaml"), 1)
        self.assertLess(parser_match.start(), branch_match.start())
        self.assertLess(
            branch_match.end(),
            deploy.index("create_scoped_token telegraf write"),
        )

        false_path = subprocess.run(
            [
                "bash",
                "-c",
                "set -eu\n"
                "docker() { printf 'bootstrap-called\\n'; }\n"
                "wait_healthy() { printf 'wait-called\\n'; }\n"
                "assert_published_port() { printf 'port-called\\n'; }\n"
                "setup_allowed=false\n"
                f"{branch}\n"
                "printf 'token-and-dependent-path\\n'\n",
            ],
            capture_output=True,
            check=False,
        )
        self.assertEqual(false_path.returncode, 0, false_path.stderr.decode())
        self.assertEqual(false_path.stdout, b"token-and-dependent-path\n")
        self.assertEqual(false_path.stderr, b"")

    def test_steady_influx_gate_allows_pinned_image_env_and_rejects_overlay_names(self) -> None:
        deploy = text("scripts/deploy.sh")
        verify = text("scripts/verify.sh")
        compose = text("compose.yaml")
        fixture = json.loads(
            text("tests/fixtures/influxdb-2.8.0-amd64-config-env-names.json")
        )
        pinned_image = (
            "docker.io/library/influxdb:2.8.0@sha256:"
            "09a5361809c771d863bcfa844a09598a82a6d9bbba1c9a9e2fa312e310572a14"
        )
        expected_baseline = [
            "PATH",
            "GOSU_VER",
            "INFLUXDB_VERSION",
            "INFLUXDB_PR",
            "INFLUXDB_PV",
            "INFLUX_CLI_VERSION",
            "INFLUX_CONFIGS_PATH",
            "INFLUXD_INIT_PORT",
            "INFLUXD_INIT_PING_ATTEMPTS",
            "DOCKER_INFLUXDB_INIT_CLI_CONFIG_NAME",
        ]
        forbidden = [
            "DOCKER_INFLUXDB_INIT_MODE",
            "DOCKER_INFLUXDB_INIT_USERNAME",
            "DOCKER_INFLUXDB_INIT_PASSWORD_FILE",
            "DOCKER_INFLUXDB_INIT_ADMIN_TOKEN_FILE",
            "DOCKER_INFLUXDB_INIT_ORG",
            "DOCKER_INFLUXDB_INIT_BUCKET",
            "DOCKER_INFLUXDB_INIT_RETENTION",
        ]
        self.assertEqual(set(fixture), {"image", "platform", "config_env_names"})
        self.assertEqual(fixture["image"], pinned_image)
        self.assertEqual(fixture["platform"], "linux/amd64")
        baseline = fixture["config_env_names"]
        self.assertEqual(baseline, expected_baseline)
        self.assertEqual(len(baseline), len(set(baseline)))
        self.assertTrue(all(re.fullmatch(r"[A-Z][A-Z0-9_]*", name) for name in baseline))
        self.assertTrue(all("=" not in name for name in baseline))
        self.assertIn("DOCKER_INFLUXDB_INIT_CLI_CONFIG_NAME", baseline)
        self.assertTrue(set(baseline).isdisjoint(forbidden))
        self.assertIn(pinned_image, compose)

        overlay = compose_service("compose.bootstrap.yaml", "influxdb")
        self.assertEqual(
            re.findall(r"(?m)^      (DOCKER_INFLUXDB_INIT_[A-Z_]+):", overlay),
            forbidden,
        )

        loop_pattern = re.compile(
            r"(?ms)^for forbidden_influx_env_name in \\\n"
            r".*?^done$"
        )
        loops: dict[str, str] = {}
        for source_name, source in (("deploy", deploy), ("verify", verify)):
            with self.subTest(source=source_name):
                loop_match = loop_pattern.search(source)
                self.assertIsNotNone(loop_match)
                loop = loop_match.group(0)
                loops[source_name] = loop
                actual_names = re.findall(
                    r"(?m)^  (DOCKER_INFLUXDB_INIT_[A-Z_]+)(?: \\|; do)$",
                    loop,
                )
                self.assertEqual(actual_names, forbidden)
                self.assertIn(
                    "{{range .Config.Env}}{{println (index (split . \"=\") 0)}}{{end}}",
                    source,
                )
                self.assertIn(
                    'if ! influx_env_names=$(docker inspect --format',
                    source,
                )
                self.assertIn(
                    'grep -Fxq -- "$forbidden_influx_env_name"',
                    loop,
                )
                self.assertIn("Unable to inspect steady InfluxDB environment names.", source)
                self.assertIn("Unable to inspect steady InfluxDB mounts.", source)
                self.assertNotIn("grep -q '^DOCKER_INFLUXDB_INIT_'", source)
                self.assertNotRegex(source, r"grep[^\n]*\^DOCKER_INFLUXDB_INIT_")
        self.assertEqual(loops["deploy"], loops["verify"])

        # Faithfully model the exact split/index name-only inspect expression
        # with sanitized values that contain additional '=' characters.
        value_marker = "sanitized-sensitive-marker=with=equals"
        raw_config_env = [f"{name}={value_marker}" for name in baseline]
        extracted_names = [entry.split("=", 1)[0] for entry in raw_config_env]
        self.assertEqual(extracted_names, baseline)
        self.assertNotIn("sanitized-sensitive-marker", "\n".join(extracted_names))

        def run_gate(names: list[str]) -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                [
                    "bash",
                    "-c",
                    "set -eu\n"
                    "influx_env_names=$1\n"
                    f"{loops['deploy']}\n"
                    "printf 'steady-env-accepted\\n'\n",
                    "bash",
                    "\n".join(names),
                ],
                capture_output=True,
                check=False,
            )

        accepted = run_gate(baseline)
        self.assertEqual(accepted.returncode, 0, accepted.stderr.decode())
        self.assertEqual(accepted.stdout, b"steady-env-accepted\n")
        self.assertEqual(accepted.stderr, b"")
        for forbidden_name in forbidden:
            with self.subTest(forbidden_name=forbidden_name):
                rejected = run_gate([*baseline, forbidden_name])
                self.assertNotEqual(rejected.returncode, 0)
                self.assertEqual(rejected.stdout, b"")
                self.assertIn(forbidden_name.encode(), rejected.stderr)

        inspect_failure = subprocess.run(
            [
                "bash",
                "-c",
                "docker() { return 42; }\n"
                "influx_container=sanitized-fixture\n"
                "if ! influx_env_names=$(docker inspect --format "
                "'{{range .Config.Env}}{{println (index (split . \"=\") 0)}}{{end}}' "
                '"$influx_container"); then exit 1; fi\n',
            ],
            capture_output=True,
            check=False,
        )
        self.assertEqual(inspect_failure.returncode, 1)

    def test_every_mqtt_cli_probe_has_a_bounded_unique_id_and_intended_diagnostic(self) -> None:
        edge = text("scripts/verify-edge-ingestion.sh")
        verify = text("scripts/verify.sh")
        restore = text("scripts/restore-test.sh")

        wrapper_call = re.compile(
            r"\bmqtt_base\s+"
            r"(?P<identity>\"?\$?[A-Za-z0-9_-]+\"?)\s+"
            r"(?P<client>\"[^\"]+\"|\$[A-Za-z0-9_]+)\s+"
            r"(?P<command>mosquitto_(?:pub|sub))"
        )
        self.assertEqual(
            [match.groups() for match in wrapper_call.finditer(edge)],
            [
                ("nodered", '"edge-ready-$mqtt_id_suffix"', "mosquitto_sub"),
                ("edsys-edge-livingroom", '"edge-pub-$mqtt_id_suffix"', "mosquitto_pub"),
                ("nodered", '"edge-replay-$mqtt_id_suffix"', "mosquitto_sub"),
                ("command-audit", '"edge-audit-$mqtt_id_suffix"', "mosquitto_sub"),
            ],
        )
        self.assertEqual(
            [match.groups() for match in wrapper_call.finditer(verify)],
            [
                ("command-audit", '"$audit_client_id"', "mosquitto_sub"),
                ('"$identity"', '"$client_id"', "mosquitto_pub"),
                ("mqtt-health", '"v-health-$mqtt_id_suffix"', "mosquitto_pub"),
                ("nodered", '"v-statepub-$mqtt_id_suffix"', "mosquitto_pub"),
                ("nodered", '"v-statesub-$mqtt_id_suffix"', "mosquitto_sub"),
                ("nodered", '"v-statedel-$mqtt_id_suffix"', "mosquitto_pub"),
                ("nodered", '"v-acksub-$mqtt_id_suffix"', "mosquitto_sub"),
                ("command-audit", '"v-cmdsub-$mqtt_id_suffix"', "mosquitto_sub"),
                ("nodered", '"v-reqpub-$mqtt_id_suffix"', "mosquitto_pub"),
                ("nodered", '"v-reqdel-$mqtt_id_suffix"', "mosquitto_pub"),
                ("command-audit", '"v-retcmd-$mqtt_id_suffix"', "mosquitto_sub"),
            ],
        )

        direct_call = re.compile(
            r"\b(mosquitto_(?:pub|sub))\s+-i\s+"
            r"(\"[^\"]+\"|\$[A-Za-z_][A-Za-z0-9_]*)"
        )
        self.assertEqual(direct_call.findall(edge), [])
        self.assertEqual(
            direct_call.findall(verify),
            [("mosquitto_pub", '"$no_cert_client_id"')],
        )
        self.assertEqual(
            direct_call.findall(restore),
            [
                ("mosquitto_pub", '"$restore_wait_client_id"'),
                ("mosquitto_pub", '"r-health-$mqtt_id_suffix"'),
                ("mosquitto_sub", '"r-audit-$mqtt_id_suffix"'),
            ],
        )

        # Every literal CLI token is accounted for by a validated wrapper call,
        # a direct `-i` invocation, or the wrapper's command allowlist itself.
        for source_name, source in (
            ("verify-edge-ingestion.sh", edge),
            ("verify.sh", verify),
            ("restore-test.sh", restore),
        ):
            unknown_lines = []
            for line_number, line in enumerate(source.splitlines(), start=1):
                if not re.search(r"\bmosquitto_(?:pub|sub)\b", line):
                    continue
                if "[[ $command == mosquitto_pub || $command == mosquitto_sub ]]" in line:
                    continue
                if "mqtt_base" in line or re.search(r"\bmosquitto_(?:pub|sub)\s+-i\b", line):
                    continue
                unknown_lines.append((line_number, line))
            self.assertEqual(unknown_lines, [], source_name)

        for source_name, source in (("edge", edge), ("verify", verify)):
            function = re.search(r"(?ms)^mqtt_base\(\) \{\n.*?^\}$", source)
            self.assertIsNotNone(function)
            body = function.group(0)
            self.assertIn('local identity=$1 client_id=$2 command=$3', body)
            self.assertIn('[[ $client_id =~ ^[a-z0-9][a-z0-9-]{0,22}$ ]]', body)
            self.assertIn('"$command" -i "$client_id" "$@"', body)

            def run_wrapper(client_id: str) -> subprocess.CompletedProcess[bytes]:
                return subprocess.run(
                    [
                        "bash",
                        "-c",
                        "docker() { printf '%s\\n' \"$*\"; }\n"
                        "network=test broker_network=test secret_root=/test mqtt_image=image\n"
                        f"{body}\n"
                        'mqtt_base identity "$1" mosquitto_pub -t test/topic\n',
                        "bash",
                        client_id,
                    ],
                    capture_output=True,
                    check=False,
                )

            for valid_id in ("a", "safe-client-1", "a" * 23):
                with self.subTest(source=source_name, valid_id=valid_id):
                    result = run_wrapper(valid_id)
                    self.assertEqual(result.returncode, 0, result.stderr.decode())
                    self.assertIn(f"mosquitto_pub -i {valid_id} ".encode(), result.stdout)
            for invalid_id in ("", "Bad", "-bad", "a" * 24):
                with self.subTest(source=source_name, invalid_id=invalid_id):
                    self.assertEqual(run_wrapper(invalid_id).returncode, 64)

        suffix = "00beef"
        edge_ids = [
            f"edge-ready-{suffix}",
            f"edge-pub-{suffix}",
            f"edge-replay-{suffix}",
            f"edge-audit-{suffix}",
        ]
        verify_ids = [
            f"v-health-{suffix}",
            f"v-nocert-{suffix}",
            *(f"v-acl{index}-{suffix}" for index in range(5)),
            *(f"v-aclm{index}-{suffix}" for index in range(5)),
            f"v-statepub-{suffix}",
            f"v-statesub-{suffix}",
            f"v-statedel-{suffix}",
            f"v-acksub-{suffix}",
            f"v-cmdsub-{suffix}",
            f"v-reqpub-{suffix}",
            f"v-reqdel-{suffix}",
            f"v-retcmd-{suffix}",
        ]
        restore_ids = [
            *(f"r-wait-{suffix}-{attempt:02d}" for attempt in range(1, 31)),
            f"r-health-{suffix}",
            f"r-audit-{suffix}",
        ]
        all_ids = [*edge_ids, *verify_ids, *restore_ids]
        self.assertEqual(len(all_ids), 56)
        self.assertEqual(len(all_ids), len(set(all_ids)))
        for client_id in all_ids:
            with self.subTest(client_id=client_id):
                self.assertRegex(client_id, r"\A[a-z0-9][a-z0-9-]*\Z")
                self.assertGreater(len(client_id.encode("ascii")), 0)
                self.assertLessEqual(len(client_id.encode("ascii")), 23)
        self.assertEqual(len(set(edge_ids[2:4])), 2)
        for index in range(5):
            self.assertNotEqual(
                f"v-acl{index}-{suffix}",
                f"v-aclm{index}-{suffix}",
            )
        self.assertEqual(
            len(
                {
                    f"v-acl{index}-{suffix}"
                    for index in range(5)
                }
                | {
                    f"v-aclm{index}-{suffix}"
                    for index in range(5)
                }
            ),
            10,
        )
        self.assertEqual(
            len(
                {
                    f"v-acksub-{suffix}",
                    f"v-cmdsub-{suffix}",
                    f"v-reqpub-{suffix}",
                }
            ),
            3,
        )

        for source in (edge, verify, restore):
            self.assertIn("mqtt_id_suffix=$(printf '%06x'", source)
            self.assertIn("[[ $mqtt_id_suffix =~ ^[0-9a-f]{6}$ ]]", source)
        for diagnostic in (
            'grep -Eiq \'zero length clientid|client identifier\' "$scratch/no-cert.err"',
            "peer did not return a certificate|alert certificate required|certificate required",
            'no_cert_started=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)',
            'docker compose logs --no-color --since "$no_cert_started" mosquitto',
            'Client $client_id received PUBACK (Mid: 1, RC:135)',
            "Warning: Publish 1 failed: Not authorized.",
            '[[ $audit_rc -eq 27 ]]',
            '! grep -Fq "Client $audit_client_id received PUBLISH " "$audit_trace"',
            "! grep -Fq 'EDSYS-AUDIT-DELIVERY:' \"$audit_trace\"",
            "mqtt_timeout_was_authenticated()",
            'grep -F "New client connected from "',
            'grep -F " as $client_id "',
            'grep -Fq "u\'$expected_identity\'"',
            'grep -Fq "Denied SUBSCRIBE from $client_id"',
            "connection (error|refused|lost)|protocol error|network error|host not found",
            "[[ $command_rc -eq 27 && ! -s \"$scratch/command.json\" ]]",
            '[[ $retained_rc -eq 27 && -z "$retained_command" ]]',
        ):
            with self.subTest(verify_diagnostic=diagnostic):
                self.assertIn(diagnostic, verify)
        self.assertNotIn("certificate|required certificate|peer did not return|tls|ssl", verify)
        self.assertLess(
            verify.index('no_cert_started=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)'),
            verify.index('mosquitto_pub -i "$no_cert_client_id"'),
        )

        no_cert_pattern = (
            "peer did not return a certificate|alert certificate required|certificate required"
        )

        def no_cert_reason_matches(message: str) -> bool:
            return subprocess.run(
                ["grep", "-Eiq", no_cert_pattern],
                input=message.encode(),
                capture_output=True,
                check=False,
            ).returncode == 0

        for message in (
            "peer did not return a certificate",
            "tlsv1 alert certificate required",
            "certificate required",
        ):
            with self.subTest(no_cert_reason=message):
                self.assertTrue(no_cert_reason_matches(message))
        for unrelated in (
            "generic tls failure",
            "ssl handshake failure",
            "connection refused",
            "host not found",
        ):
            with self.subTest(unrelated_no_cert_reason=unrelated):
                self.assertFalse(no_cert_reason_matches(unrelated))

        timeout_function = re.search(
            r"(?ms)^mqtt_timeout_was_authenticated\(\) \{\n.*?^\}$",
            verify,
        )
        self.assertIsNotNone(timeout_function)
        edge_timeout_function = re.search(
            r"(?ms)^mqtt_timeout_was_authenticated\(\) \{\n.*?^\}$",
            edge,
        )
        self.assertIsNotNone(edge_timeout_function)
        session_function = re.search(
            r"(?ms)^mqtt_session_was_authenticated\(\) \{\n.*?^\}$",
            verify,
        )
        self.assertIsNotNone(session_function)
        for function_body in (timeout_function.group(0), edge_timeout_function.group(0)):
            for contract in (
                "grep -Fq 'Timed out'",
                "zero length clientid|client identifier|not authori[sz]ed|certificate|tls|ssl",
                'grep -Fq "Denied SUBSCRIBE from $client_id"',
            ):
                self.assertIn(contract, function_body)
        self.assertIn(
            '"$client_id" "$expected_identity" "$started" "$log_file"',
            timeout_function.group(0),
        )
        for contract in (
            'docker compose logs --no-color --since "$started" mosquitto',
            'grep -F "New client connected from "',
            'grep -F " as $client_id "',
            'grep -Fq "u\'$expected_identity\'"',
        ):
            self.assertIn(contract, session_function.group(0))
        for contract in (
            'docker compose logs --no-color --since "$started" mosquitto',
            'grep -Fq " as $client_id "',
        ):
            self.assertIn(contract, edge_timeout_function.group(0))

        def run_timeout_gate(error: str, broker_log: str) -> int:
            with tempfile.TemporaryDirectory() as temporary:
                error_path = Path(temporary, "probe.err")
                log_path = Path(temporary, "probe.log")
                error_path.write_text(error, encoding="utf-8")
                result = subprocess.run(
                    [
                        "bash",
                        "-c",
                        "docker() { printf '%s\\n' \"$MOCK_BROKER_LOG\"; }\n"
                        "sleep() { SECONDS=$((SECONDS + 1)); }\n"
                        f"{session_function.group(0)}\n{timeout_function.group(0)}\n"
                        'mqtt_timeout_was_authenticated "$1" audit-test command-audit '
                        '2026-08-22T12:00:00.000000000Z "$2"\n',
                        "bash",
                        str(error_path),
                        str(log_path),
                    ],
                    capture_output=True,
                    check=False,
                    env={
                        "PATH": "/usr/bin:/bin",
                        "MOCK_BROKER_LOG": broker_log,
                    },
                )
                return result.returncode

        accepted_session = (
            "New client connected from 172.20.0.2 as audit-test "
            "(p5, c1, k60, u'command-audit')"
        )
        self.assertEqual(run_timeout_gate("Timed out\n", accepted_session), 0)
        for error in (
            "Timed out\nConnection error: Not authorized\n",
            "Timed out\nTLS certificate failure\n",
            "Timed out\nzero length clientid\n",
            "Timed out\nhost not found\n",
        ):
            with self.subTest(timeout_error=error):
                self.assertNotEqual(run_timeout_gate(error, accepted_session), 0)
        for broker_log in (
            "",
            accepted_session + "\nDenied SUBSCRIBE from audit-test",
            accepted_session + "\nClient audit-test disconnected due to protocol error",
        ):
            with self.subTest(timeout_broker_log=broker_log):
                self.assertNotEqual(run_timeout_gate("Timed out\n", broker_log), 0)

        for source, started, client_id, error_path in (
            (
                edge,
                'command_probe_started=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)',
                '"edge-audit-$mqtt_id_suffix"',
                '"$scratch/command.err"',
            ),
            (
                verify,
                'command_probe_started=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)',
                '"v-cmdsub-$mqtt_id_suffix"',
                '"$scratch/command.err"',
            ),
            (
                verify,
                'post_restart_probe_started=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)',
                '"v-retcmd-$mqtt_id_suffix"',
                '"$scratch/post-restart.err"',
            ),
        ):
            self.assertIn("-eq 27", source)
            self.assertIn(started, source)
            self.assertIn("mqtt_timeout_was_authenticated", source)
            self.assertIn(client_id, source)
            self.assertIn(error_path, source)
        for restore_contract in (
            '[[ $retained_rc -eq 27 && -z "$retained_output" ]]',
            "grep -Fq 'Timed out' \"$test_dir/retained-command.err\"",
            "zero length clientid|client identifier|not authori[sz]ed|certificate|tls|ssl",
            'docker logs "$mosquitto_container" >"$test_dir/retained-command.log"',
            'grep -Fq " as r-audit-$mqtt_id_suffix "',
            'grep -Fq "Denied SUBSCRIBE from r-audit-$mqtt_id_suffix"',
        ):
            self.assertIn(restore_contract, restore)

    def test_acl_denial_requires_exact_puback_and_authenticated_zero_delivery(self) -> None:
        verify = text("scripts/verify.sh")
        session_match = re.search(
            r"(?ms)^mqtt_session_was_authenticated\(\) \{\n.*?^\}$",
            verify,
        )
        timeout_match = re.search(
            r"(?ms)^mqtt_timeout_was_authenticated\(\) \{\n.*?^\}$",
            verify,
        )
        acl_match = re.search(
            r"(?ms)^mqtt_acl_publish_must_fail\(\) \{\n.*?^\}$",
            verify,
        )
        self.assertIsNotNone(session_match)
        self.assertIsNotNone(timeout_match)
        self.assertIsNotNone(acl_match)
        session_helper = session_match.group(0)
        timeout_helper = timeout_match.group(0)
        acl_helper = acl_match.group(0)

        for contract in (
            "local identity=$1 client_id=$2 audit_client_id=$3 topic=$4",
            'mqtt_base command-audit "$audit_client_id" mosquitto_sub',
            '-W 10 -C 1',
            "-F 'EDSYS-AUDIT-DELIVERY:%p' -t \"$topic\"",
            '>"$audit_trace" 2>"$audit_error" &',
            'mqtt_session_was_authenticated \\\n      "$audit_client_id" command-audit "$audit_started"',
            'grep -Fqx "Client $audit_client_id received SUBACK" "$audit_trace"',
            "grep -Fqx 'Subscribed (mid: 1): 1' \"$audit_trace\"",
            'kill -0 "$audit_pid"',
            'mqtt_base "$identity" "$client_id" mosquitto_pub',
            '-d -h mosquitto -p 8883 -V mqttv5 -q 1 -t "$topic"',
            'mqtt_session_was_authenticated \\\n      "$client_id" "$identity" "$publisher_started"',
            'grep -Fxc "Client $client_id received PUBACK (Mid: 1, RC:135)"',
            "grep -Fxc 'Warning: Publish 1 failed: Not authorized.'",
            "grep -Fc ' received PUBACK '",
            '[[ $audit_rc -eq 27 ]]',
            '! grep -Fq "Client $audit_client_id received PUBLISH " "$audit_trace"',
            "! grep -Fq 'EDSYS-AUDIT-DELIVERY:' \"$audit_trace\"",
            'mqtt_timeout_was_authenticated \\\n        "$audit_error" "$audit_client_id" command-audit',
        ):
            with self.subTest(acl_contract=contract):
                self.assertIn(contract, acl_helper)
        self.assertLess(
            acl_helper.index('mqtt_base command-audit "$audit_client_id" mosquitto_sub'),
            acl_helper.index('mqtt_session_was_authenticated \\\n      "$audit_client_id" command-audit'),
        )
        self.assertLess(
            acl_helper.index('mqtt_session_was_authenticated \\\n      "$audit_client_id" command-audit'),
            acl_helper.index('mqtt_base "$identity" "$client_id" mosquitto_pub'),
        )
        self.assertNotIn("Denied PUBLISH from", verify)
        self.assertNotIn("publisher_rc", acl_helper)
        self.assertNotIn("if (( rc == 0 ))", acl_helper)
        self.assertIn(
            'mqtt_acl_publish_must_fail "$identity" "v-acl${acl_index}-$mqtt_id_suffix" \\\n'
            '    "v-aclm${acl_index}-$mqtt_id_suffix"',
            verify,
        )

        exact_publisher_diagnostic = (
            "Client publisher-test received PUBACK (Mid: 1, RC:135)\n"
            "Warning: Publish 1 failed: Not authorized.\n"
        )
        accepted_log = (
            "New client connected from 172.20.0.2 as audit-test "
            "(p5, c1, k60, u'command-audit')\n"
            "New client connected from 172.20.0.3 as publisher-test "
            "(p5, c1, k60, u'identity')"
        )

        def run_acl_helper(
            *,
            publisher_output: str = exact_publisher_diagnostic,
            publisher_rc: int = 0,
            audit_output: str = "",
            audit_error: str = "Timed out\n",
            audit_rc: int = 27,
            broker_log: str = accepted_log,
        ) -> subprocess.CompletedProcess[bytes]:
            with tempfile.TemporaryDirectory() as temporary:
                release_file = Path(temporary, "release-audit")
                program = (
                    "docker() { printf '%s\\n' \"$MOCK_BROKER_LOG\"; }\n"
                    "sleep() { SECONDS=$((SECONDS + 1)); }\n"
                        "mqtt_base() {\n"
                        "  local identity=$1 client_id=$2 command=$3\n"
                        "  if [[ $command == mosquitto_sub ]]; then\n"
                        "    printf 'Client %s received SUBACK\\n' \"$client_id\"\n"
                        "    printf 'Subscribed (mid: 1): 1\\n'\n"
                        "    while [[ ! -e $MOCK_RELEASE_FILE ]]; do /bin/sleep 0.01; done\n"
                    "    printf '%s' \"$MOCK_AUDIT_OUTPUT\"\n"
                    "    printf '%s' \"$MOCK_AUDIT_ERROR\" >&2\n"
                    "    return \"$MOCK_AUDIT_RC\"\n"
                    "  fi\n"
                    "  : >\"$MOCK_RELEASE_FILE\"\n"
                    "  printf '%s' \"$MOCK_PUBLISHER_OUTPUT\"\n"
                    "  return \"$MOCK_PUBLISHER_RC\"\n"
                    "}\n"
                    f"{session_helper}\n{timeout_helper}\n{acl_helper}\n"
                    "scratch=$1\n"
                    "mqtt_acl_publish_must_fail identity publisher-test audit-test "
                    "edsys/v1/command/ha/verification/nonexistent\n"
                )
                return subprocess.run(
                    ["bash", "-c", program, "bash", temporary],
                    capture_output=True,
                    check=False,
                    env={
                        "PATH": "/usr/bin:/bin",
                        "MOCK_RELEASE_FILE": str(release_file),
                        "MOCK_PUBLISHER_OUTPUT": publisher_output,
                        "MOCK_PUBLISHER_RC": str(publisher_rc),
                        "MOCK_AUDIT_OUTPUT": audit_output,
                        "MOCK_AUDIT_ERROR": audit_error,
                        "MOCK_AUDIT_RC": str(audit_rc),
                        "MOCK_BROKER_LOG": broker_log,
                    },
                )

        accepted = run_acl_helper()
        self.assertEqual(accepted.returncode, 0, accepted.stderr.decode())
        # The pinned MQTT v5 CLI may return either status; the exact protocol
        # diagnostic and observed non-delivery, not process status, decide.
        accepted_nonzero = run_acl_helper(publisher_rc=7)
        self.assertEqual(accepted_nonzero.returncode, 0, accepted_nonzero.stderr.decode())

        rejected_cases = {
            "missing PUBACK": {
                "publisher_output": "Warning: Publish 1 failed: Not authorized.\n",
            },
            "wrong PUBACK reason": {
                "publisher_output": (
                    "Client publisher-test received PUBACK (Mid: 1, RC:0)\n"
                    "Warning: Publish 1 failed: Not authorized.\n"
                ),
            },
            "missing warning": {
                "publisher_output": "Client publisher-test received PUBACK (Mid: 1, RC:135)\n",
            },
            "duplicate PUBACK": {
                "publisher_output": exact_publisher_diagnostic
                + "Client publisher-test received PUBACK (Mid: 2, RC:135)\n",
            },
            "delivered bytes": {
                "audit_output": (
                    "Client audit-test received PUBLISH (d0, q1, r0, m1, "
                    "'edsys/v1/command/ha/verification/nonexistent', ... (25 bytes))\n"
                    'EDSYS-AUDIT-DELIVERY:{"unexpected":"delivery"}\n'
                ),
            },
            "audit not authenticated": {
                "broker_log": (
                    "New client connected from 172.20.0.3 as publisher-test "
                    "(p5, c1, k60, u'identity')"
                ),
            },
            "wrong audit identity": {
                "broker_log": accepted_log.replace("u'command-audit'", "u'nodered'"),
            },
            "audit ACL denied": {
                "broker_log": accepted_log + "\nDenied SUBSCRIBE from audit-test",
            },
            "audit authentication error": {
                "audit_error": "Timed out\nConnection error: Not authorized\n",
            },
            "audit TLS error": {"audit_error": "Timed out\nTLS certificate failure\n"},
            "audit transport error": {"audit_error": "Timed out\nConnection refused\n"},
            "publisher not authenticated": {
                "broker_log": (
                    "New client connected from 172.20.0.2 as audit-test "
                    "(p5, c1, k60, u'command-audit')"
                ),
            },
            "wrong publisher identity": {
                "broker_log": accepted_log.replace("u'identity'", "u'nodered'"),
            },
        }
        for label, overrides in rejected_cases.items():
            with self.subTest(rejected=label):
                result = run_acl_helper(**overrides)
                self.assertNotEqual(result.returncode, 0, result.stderr.decode())

    def test_both_noninternal_plane_subnets_are_overlap_gated_before_compose(self) -> None:
        deploy = text("scripts/deploy.sh")
        for contract in (
            "readonly ingress_network=edsys-edcore-automation-ingress",
            "readonly ingress_bridge=br-ed-ingress",
            "readonly egress_network=edsys-edcore-automation-egress",
            "readonly egress_bridge=br-edsys-egress",
            "select(.Name != $egress and .Name != $ingress)",
            "select(.dev != $egress and .dev != $ingress)",
            'ipaddress.ip_network("172.31.82.0/28")',
            'ipaddress.ip_network("172.31.82.16/29")',
            "candidate.overlaps(target)",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, deploy)
        self.assertLess(
            deploy.index('ipaddress.ip_network("172.31.82.16/29")'),
            deploy.index("docker compose config --quiet"),
        )

    def test_existing_named_ingress_network_is_fail_closed_before_compose(self) -> None:
        deploy = text("scripts/deploy.sh")
        verify = text("scripts/verify.sh")
        preflight_contracts = (
            'ingress_count=$(jq -r --arg network "$ingress_network"',
            'select(.Name == $network)',
            ".Driver == \"bridge\"",
            '.Scope == "local"',
            ".Internal == false",
            ".Attachable == false",
            ".Ingress == false",
            ".ConfigOnly == false",
            ".EnableIPv6 == false",
            ".Options == {",
            '"com.docker.network.bridge.enable_icc": "false"',
            '"com.docker.network.bridge.name": $bridge',
            '.IPAM.Driver == "default"',
            "(.IPAM.Options == null or .IPAM.Options == {})",
            ".IPAM.Config == [{",
            '"Subnet": "172.31.82.16/29"',
            '"IPRange": ""',
            '"Gateway": "172.31.82.17"',
            '.Labels["com.docker.compose.project"] == "edsys-edcore-automation"',
            '.Labels["com.docker.compose.network"] == "ingress"',
            'readonly mosquitto_container=edsys-edcore-automation-mosquitto-1',
            'readonly influxdb_container=edsys-edcore-automation-influxdb-1',
            '(.value.Name == $mosquitto',
            '.value.IPv4Address == "172.31.82.18/29"',
            '(.value.Name == $influxdb',
            '.value.IPv4Address == "172.31.82.19/29"',
            '(.value.IPv6Address // "") == ""',
            '((.Containers // {}) | to_entries | length) <= 2',
            'ip link show dev "$ingress_bridge"',
            "inspect it without deleting it",
        )
        for contract in preflight_contracts:
            with self.subTest(contract=contract):
                self.assertIn(contract, deploy)

        preflight = deploy.index(
            'ingress_count=$(jq -r --arg network "$ingress_network"'
        )
        self.assertLess(preflight, deploy.index('"$stack_dir/scripts/install-firewall.sh" --apply'))
        self.assertLess(preflight, deploy.index("docker compose"))
        self.assertNotRegex(
            deploy,
            r"(?m)^\s*docker (?:network (?:rm|disconnect|prune)|compose down)\b",
        )

        two_shape_ipam = re.compile(
            r'(?s)(?:\.\[0\])?\.IPAM\.Config == \[\{\s*'
            r'"Subnet": "172\.31\.82\.16/29",\s*'
            r'"Gateway": "172\.31\.82\.17"\s*'
            r'\}\]\s*or\s*'
            r'(?:\.\[0\])?\.IPAM\.Config == \[\{\s*'
            r'"Subnet": "172\.31\.82\.16/29",\s*'
            r'"IPRange": "",\s*'
            r'"Gateway": "172\.31\.82\.17"\s*'
            r'\}\]',
        )
        for source_name, source in (("deploy", deploy), ("verify", verify)):
            with self.subTest(source=source_name):
                self.assertRegex(source, two_shape_ipam)
                self.assertEqual(source.count('"IPRange": ""'), 1)

    def test_docker29_ingress_fixture_matches_fail_closed_preflight_semantics(self) -> None:
        network_name = "edsys-edcore-automation-ingress"
        mosquitto = "edsys-edcore-automation-mosquitto-1"
        influxdb = "edsys-edcore-automation-influxdb-1"
        fixture_text = text("tests/fixtures/docker29-ingress-network-inspect.json")
        fixture = json.loads(fixture_text)
        self.assertIsInstance(fixture, list)
        self.assertEqual(len(fixture), 1)
        baseline = fixture[0]

        self.assertEqual(
            set(baseline),
            {
                "Name",
                "Id",
                "Created",
                "Scope",
                "Driver",
                "EnableIPv4",
                "EnableIPv6",
                "IPAM",
                "Internal",
                "Attachable",
                "Ingress",
                "ConfigFrom",
                "ConfigOnly",
                "Containers",
                "Options",
                "Labels",
            },
        )
        self.assertEqual(baseline["Created"], "2000-01-01T00:00:00Z")
        self.assertEqual(
            baseline["Id"],
            "0000000000000000000000000000000000000000000000000000000000000082",
        )
        self.assertEqual(
            set(baseline["Containers"]),
            {"a" * 64, "b" * 64},
        )
        self.assertEqual(
            {
                endpoint["EndpointID"]
                for endpoint in baseline["Containers"].values()
            },
            {"c" * 64, "d" * 64},
        )
        self.assertEqual(
            {
                endpoint["MacAddress"]
                for endpoint in baseline["Containers"].values()
            },
            {"02:00:00:00:00:12", "02:00:00:00:00:13"},
        )
        self.assertEqual(
            baseline["Labels"]["com.docker.compose.config-hash"],
            "sanitized-fixture",
        )
        self.assertEqual(
            baseline["Labels"]["com.docker.compose.version"],
            "sanitized-fixture",
        )
        self.assertNotRegex(fixture_text, r"(?i)password|token|api[_-]?key|secret")

        legacy_ipam = [
            {
                "Subnet": "172.31.82.16/29",
                "Gateway": "172.31.82.17",
            }
        ]
        docker29_ipam = [
            {
                "Subnet": "172.31.82.16/29",
                "IPRange": "",
                "Gateway": "172.31.82.17",
            }
        ]

        def accepted(snapshot: list[dict[str, object]]) -> bool:
            matches = [item for item in snapshot if item.get("Name") == network_name]
            if len(matches) != 1:
                return False
            network = matches[0]
            ipam = network.get("IPAM")
            labels = network.get("Labels")
            if not (
                network.get("Driver") == "bridge"
                and network.get("Scope") == "local"
                and network.get("Internal") is False
                and network.get("Attachable") is False
                and network.get("Ingress") is False
                and network.get("ConfigOnly") is False
                and network.get("EnableIPv6") is False
                and network.get("Options")
                == {
                    "com.docker.network.bridge.enable_icc": "false",
                    "com.docker.network.bridge.name": "br-ed-ingress",
                }
                and isinstance(ipam, dict)
                and ipam.get("Driver") == "default"
                and ipam.get("Options") in (None, {})
                and ipam.get("Config") in (legacy_ipam, docker29_ipam)
                and isinstance(labels, dict)
                and labels.get("com.docker.compose.project")
                == "edsys-edcore-automation"
                and labels.get("com.docker.compose.network") == "ingress"
            ):
                return False
            containers = network.get("Containers") or {}
            if not isinstance(containers, dict) or len(containers) > 2:
                return False
            expected = {
                mosquitto: "172.31.82.18/29",
                influxdb: "172.31.82.19/29",
            }
            return all(
                isinstance(endpoint, dict)
                and endpoint.get("Name") in expected
                and endpoint.get("IPv4Address") == expected[endpoint["Name"]]
                and (endpoint.get("IPv6Address") or "") == ""
                for endpoint in containers.values()
            )

        self.assertTrue(accepted(fixture))
        without_iprange = copy.deepcopy(baseline)
        without_iprange["IPAM"]["Config"][0].pop("IPRange")
        self.assertTrue(accepted([without_iprange]))
        detached = copy.deepcopy(baseline)
        detached["Containers"] = {}
        self.assertTrue(accepted([detached]))
        empty_ipam_options = copy.deepcopy(baseline)
        empty_ipam_options["IPAM"]["Options"] = {}
        self.assertTrue(accepted([empty_ipam_options]))

        rejected: dict[str, list[dict[str, object]]] = {
            "missing named network": [],
            "duplicate named network": [baseline, copy.deepcopy(baseline)],
        }
        scalar_drift = {
            "driver": ("Driver", "overlay"),
            "scope": ("Scope", "swarm"),
            "internal": ("Internal", True),
            "attachable": ("Attachable", True),
            "ingress": ("Ingress", True),
            "config-only": ("ConfigOnly", True),
            "ipv6": ("EnableIPv6", True),
        }
        for label, (field, value) in scalar_drift.items():
            candidate = copy.deepcopy(baseline)
            candidate[field] = value
            rejected[label] = [candidate]

        mosquitto_id = "a" * 64
        influxdb_id = "b" * 64
        for label, path, value in (
            ("extra option", ("Options", "unexpected"), "true"),
            ("wrong bridge", ("Options", "com.docker.network.bridge.name"), "br-other"),
            ("wrong IPAM driver", ("IPAM", "Driver"), "custom"),
            ("unexpected IPAM options", ("IPAM", "Options"), {"unexpected": "true"}),
            ("nonempty IPRange", ("IPAM", "Config", 0, "IPRange"), "172.31.82.18/32"),
            ("null IPRange", ("IPAM", "Config", 0, "IPRange"), None),
            ("extra IPAM config key", ("IPAM", "Config", 0, "AuxAddress"), ""),
            ("wrong subnet", ("IPAM", "Config", 0, "Subnet"), "172.31.82.24/29"),
            ("wrong gateway", ("IPAM", "Config", 0, "Gateway"), "172.31.82.18"),
            ("wrong project", ("Labels", "com.docker.compose.project"), "foreign"),
            ("wrong network label", ("Labels", "com.docker.compose.network"), "other"),
            ("wrong endpoint name", ("Containers", mosquitto_id, "Name"), "foreign"),
            ("wrong endpoint address", ("Containers", influxdb_id, "IPv4Address"), "172.31.82.20/29"),
            ("endpoint IPv6", ("Containers", influxdb_id, "IPv6Address"), "fd00::19/64"),
        ):
            candidate = copy.deepcopy(baseline)
            target: object = candidate
            for key in path[:-1]:
                target = target[key]  # type: ignore[index]
            target[path[-1]] = value  # type: ignore[index]
            rejected[label] = [candidate]

        second_ipam_entry = copy.deepcopy(baseline)
        second_ipam_entry["IPAM"]["Config"].append(copy.deepcopy(docker29_ipam[0]))
        rejected["second IPAM config entry"] = [second_ipam_entry]

        extra_endpoint = copy.deepcopy(baseline)
        extra_endpoint["Containers"]["e" * 64] = {
            "Name": "foreign",
            "IPv4Address": "172.31.82.20/29",
            "IPv6Address": "",
        }
        rejected["extra endpoint"] = [extra_endpoint]

        for label, snapshot in rejected.items():
            with self.subTest(label=label):
                self.assertFalse(accepted(snapshot))

    def test_guest_firewall_exposes_only_mqtt_to_the_lan(self) -> None:
        bootstrap = text("scripts/bootstrap-guest.sh")
        firewall = text("firewall/edsys-automation-firewall.nft.in")
        for ssh_contract in (
            "PasswordAuthentication no",
            "KbdInteractiveAuthentication no",
            "PermitRootLogin no",
            "AllowUsers jeremy edsys-backup",
        ):
            with self.subTest(ssh_contract=ssh_contract):
                self.assertIn(ssh_contract, bootstrap)
        self.assertIn(
            'ip saddr 192.168.50.0/24 tcp dport 8883 accept',
            firewall,
        )
        self.assertIn(
            'ip saddr 192.168.50.50 tcp dport { 22, 1880, 8086 } accept',
            firewall,
        )
        self.assertNotIn(
            'ip saddr 192.168.50.0/24 tcp dport { 22, 1880, 8883 }',
            firewall,
        )
        self.assertNotIn("tcp dport 8884 accept", firewall)
        for ingress_contract in (
            'ip daddr 172.31.82.18 tcp dport 8883 accept',
            'ip daddr 172.31.82.19 tcp dport 8086 accept',
            'oifname "br-ed-ingress" counter drop',
            'iifname "br-ed-ingress" limit rate 5/minute log prefix "edsys-ingress-drop " counter drop',
            'iifname "br-ed-ingress" counter drop',
        ):
            with self.subTest(ingress_contract=ingress_contract):
                self.assertIn(ingress_contract, firewall)

        input_chain = re.search(r"(?ms)^  chain input \{\n(.*?)^  \}$", firewall).group(1)
        forward_chain = re.search(r"(?ms)^  chain forward \{\n(.*?)^  \}$", firewall).group(1)
        self.assertLess(
            input_chain.index("ct state established,related accept"),
            input_chain.index('iifname "br-ed-ingress" counter drop'),
        )
        self.assertLess(
            input_chain.index('iifname "br-ed-ingress" counter drop'),
            input_chain.index("ip protocol icmp accept"),
        )
        self.assertLess(
            forward_chain.index("ct state established,related accept"),
            forward_chain.index('iifname "br-ed-ingress" counter drop'),
        )
        direct_ingress_drop = "ip daddr 172.31.82.16/29 counter drop"
        self.assertIn(direct_ingress_drop, forward_chain)
        direct_drop_position = forward_chain.index(direct_ingress_drop)
        outbound_ingress_drop = 'oifname "br-ed-ingress" counter drop'
        outbound_drop_position = forward_chain.index(outbound_ingress_drop)
        for prior_contract in (
            "ip daddr 172.31.82.18 tcp dport 8883 accept",
            "ip daddr 172.31.82.2 tcp dport 1880 accept",
            "ip daddr 172.31.82.19 tcp dport 8086 accept",
            "ct status dnat ct original ip daddr 192.168.50.82",
        ):
            with self.subTest(prior_contract=prior_contract):
                self.assertLess(forward_chain.index(prior_contract), outbound_drop_position)
                self.assertLess(forward_chain.index(prior_contract), direct_drop_position)
        self.assertLess(outbound_drop_position, direct_drop_position)
        for egress_contract in (
            'iifname "br-edsys-egress" ip saddr 172.31.82.2 ip daddr { 192.168.50.5, 192.168.50.6 } udp dport 53 accept',
            'iifname "br-edsys-egress" ip saddr 172.31.82.2 ip daddr { 192.168.50.5, 192.168.50.6 } tcp dport 53 accept',
            'iifname "br-edsys-egress" ip saddr 172.31.82.2 ip daddr 192.168.50.75 tcp dport 8123 accept',
            'iifname "br-edsys-egress" limit rate 5/minute log prefix "edsys-egress-drop " counter drop',
            'iifname "br-edsys-egress" counter drop',
        ):
            with self.subTest(egress_contract=egress_contract):
                self.assertIn(egress_contract, firewall)
        self.assertNotIn("systemctl enable edsys-automation-compose.service", bootstrap)
        self.assertNotIn("systemctl enable --now edsys-automation-backup.timer", bootstrap)

    def test_tracked_firewall_installer_runs_before_compose_and_uses_tracked_unit(self) -> None:
        bootstrap = text("scripts/bootstrap-guest.sh")
        deploy = text("scripts/deploy.sh")
        installer = text("scripts/install-firewall.sh")
        template = text("firewall/edsys-automation-firewall.nft.in")
        firewall_unit = text("systemd/edsys-automation-firewall.service")
        installer_call = '"$stack_dir/scripts/install-firewall.sh" --apply'

        self.assertEqual(template.count("@LAN_IFACE@"), 5)
        for installer_contract in (
            "firewall/edsys-automation-firewall.nft.in",
            'grep -o \'@LAN_IFACE@\'',
            "source-guard.sh",
            "readonly ingress_bridge=br-ed-ingress",
            "readonly egress_bridge=br-edsys-egress",
            '[[ $bridge =~ ^[a-z0-9][a-z0-9-]*$ && ${#bridge} -le 15 ]]',
            '[[ $lan_iface =~ ^[[:alnum:]_.:-]+$ && ${#lan_iface} -le 15 ]]',
            "192.168.50.82/24",
            '$(grep -o \'@LAN_IFACE@\' "$template" | wc -l) -eq 5',
            'sed "s|@LAN_IFACE@|$lan_iface|g"',
            '$(grep -c \'@LAN_IFACE@\' "$candidate") -eq 0',
            'install -o root -g root -m 0755 "$stack_dir/scripts/firewall-apply.sh"',
            "active_fingerprint()",
            "nft -j list table inet edsys_automation_filter",
            "jq -cS",
            ".counter |= del(.packets, .bytes)",
            "active_before=$(active_fingerprint)",
            'install -o root -g root -m 0644 "$candidate" "$next"',
            'mv -f -- "$next" "$canonical"',
            'if ! "$installed_apply"; then',
            'install -o root -g root -m 0644 "$previous" "$next"',
            'rm -f -- "$canonical"',
            "active_after=$(active_fingerprint)",
            '[[ $active_after == "$active_before" ]]',
            "New firewall failed to apply; deployment remains stopped before Compose attachment.",
        ):
            with self.subTest(installer_contract=installer_contract):
                self.assertIn(installer_contract, installer)
        self.assertNotIn('nft -c -f "$candidate"', installer)
        self.assertEqual(installer.count('if ! "$installed_apply"; then'), 1)

        guard_call = '"$stack_dir/scripts/source-guard.sh" "$guard_phase"'
        interface_validation = (
            '[[ $lan_iface =~ ^[[:alnum:]_.:-]+$ && ${#lan_iface} -le 15 ]]'
        )
        placeholder_validation = '$(grep -o \'@LAN_IFACE@\' "$template" | wc -l) -eq 5'
        render = 'sed "s|@LAN_IFACE@|$lan_iface|g"'
        helper_install = 'install -o root -g root -m 0755 "$stack_dir/scripts/firewall-apply.sh"'
        snapshot = "active_before=$(active_fingerprint)"
        canonical_stage = 'install -o root -g root -m 0644 "$candidate" "$next"'
        helper_apply = 'if ! "$installed_apply"; then'
        restore = 'install -o root -g root -m 0644 "$previous" "$next"'
        compare = '[[ $active_after == "$active_before" ]]'
        for earlier, later in (
            (guard_call, interface_validation),
            (interface_validation, placeholder_validation),
            (placeholder_validation, render),
            (render, helper_install),
            (helper_install, snapshot),
            (snapshot, canonical_stage),
            (canonical_stage, helper_apply),
            (helper_apply, restore),
            (restore, compare),
        ):
            with self.subTest(order=(earlier, later)):
                self.assertLess(installer.index(earlier), installer.index(later))

        failure_body = installer.split(helper_apply, 1)[1].split("\nfi", 1)[0]
        self.assertNotIn('"$installed_apply"', failure_body)

        self.assertIn(installer_call, bootstrap)
        self.assertIn(installer_call, deploy)
        self.assertNotIn("cat >/etc/edsys-automation-firewall.nft", bootstrap)
        self.assertNotIn("cat >/etc/systemd/system/edsys-automation-firewall.service", bootstrap)
        self.assertIn('for unit in "$stack_dir"/systemd/*', bootstrap)
        self.assertLess(
            bootstrap.index(installer_call),
            bootstrap.index("systemctl enable --now edsys-automation-firewall.service"),
        )
        self.assertLess(
            bootstrap.index('for unit in "$stack_dir"/systemd/*'),
            bootstrap.index("systemctl enable --now edsys-automation-firewall.service"),
        )
        self.assertLess(deploy.index(installer_call), deploy.index("docker compose"))
        self.assertLess(deploy.index(installer_call), deploy.index("docker compose config --quiet"))
        self.assertLess(deploy.index(installer_call), deploy.index("docker compose up"))
        for unit_contract in (
            f"ExecStartPre={INSTALLED_SOURCE_GUARD} --coherent",
            "ExecStart=/usr/local/sbin/edsys-automation-firewall",
            "ExecReload=/usr/local/sbin/edsys-automation-firewall",
        ):
            self.assertIn(unit_contract, firewall_unit)

    def test_verify_proves_effective_publication_and_ingress_isolation(self) -> None:
        verify = text("scripts/verify.sh")
        for daemon_contract in (
            '.["userland-proxy"] == false',
            '(has("allow-direct-routing") | not)',
            "/etc/docker/daemon.json",
        ):
            with self.subTest(daemon_contract=daemon_contract):
                self.assertIn(daemon_contract, verify)
        for topology_contract in (
            "edsys-edcore-automation-ingress",
            '.[0].Internal == false',
            '.[0].EnableIPv6 == false',
            'bridge.name"] == "br-ed-ingress"',
            'bridge.enable_icc"] == "false"',
            'IPAddress == "172.31.82.18"',
            'IPAddress == "172.31.82.19"',
            'IPv4Address == "172.31.82.18/29"',
            'IPv4Address == "172.31.82.19/29"',
        ):
            with self.subTest(topology_contract=topology_contract):
                self.assertIn(topology_contract, verify)

        for denial_contract in (
            "Mosquitto reached InfluxDB laterally over ingress",
            "InfluxDB reached Mosquitto laterally over ingress",
            "Ingress service reached LAN DNS",
            "Ingress service reached Home Assistant",
            "Ingress service reached a host service",
            "Ingress service reached the Internet",
        ):
            with self.subTest(denial_contract=denial_contract):
                self.assertIn(denial_contract, verify)

        for mapping_contract in (
            '.[0].HostConfig.PortBindings[$key] == [{"HostIp": $host, "HostPort": $port}]',
            '.[0].NetworkSettings.Ports[$key] == [{"HostIp": $host, "HostPort": $port}]',
            "assert_published_port node-red 1880",
            "assert_published_port influxdb 8086",
            "assert_published_port mosquitto 8883",
            '.[0].NetworkSettings.Ports["8884/tcp"] == null',
        ):
            with self.subTest(mapping_contract=mapping_contract):
                self.assertIn(mapping_contract, verify)
        self.assertNotRegex(verify, r"(?m)^\s*ss\s+-H\s+-ltn\b")

    def test_timers_are_enabled_only_after_full_live_verification(self) -> None:
        verify = text("scripts/verify.sh")
        self.assertIn("readonly network=edsys-edcore-automation-broker", verify)
        self.assertNotIn("readonly network=edsys-edcore-automation\n", verify)
        self.assertIn("--retained-only", verify)
        self.assertIn("retained_request", verify)
        self.assertIn("docker compose restart -t 30 mosquitto", verify)
        self.assertIn("edsys/v1/command/ha/#", verify)
        for egress_verification in (
            'IPAddress == "172.31.82.2"',
            'bridge.name"] == "br-edsys-egress"',
            'Subnet == "172.31.82.0/28"',
            'Gateway == "172.31.82.1"',
            'host:"192.168.50.75",port:8123',
            'host:"1.1.1.1",port:443',
            "Node-RED bypassed the reviewed HA/DNS-only egress boundary.",
        ):
            with self.subTest(egress_verification=egress_verification):
                self.assertIn(egress_verification, verify)
        self.assertIn(
            "systemctl enable --now edsys-automation-backup.timer edsys-automation-restore-test.timer",
            verify,
        )

    def test_root_systemd_services_have_exact_guarded_path_coverage(self) -> None:
        expected_commands = {
            "edsys-automation-backup.service": {
                "ExecStartPre": f"{INSTALLED_SOURCE_GUARD} --runtime",
                "ExecStart": f"{CANONICAL_STACK}/scripts/backup.sh",
            },
            "edsys-automation-compose.service": {
                "ExecStartPre": f"{INSTALLED_SOURCE_GUARD} --runtime",
                "ExecStart": "/usr/bin/docker compose up -d --remove-orphans",
                "ExecStop": "/usr/bin/docker compose stop -t 90",
            },
            "edsys-automation-firewall.service": {
                "ExecStartPre": f"{INSTALLED_SOURCE_GUARD} --coherent",
                "ExecStart": "/usr/local/sbin/edsys-automation-firewall",
                "ExecReload": "/usr/local/sbin/edsys-automation-firewall",
            },
            "edsys-automation-restore-test.service": {
                "ExecStartPre": f"{INSTALLED_SOURCE_GUARD} --runtime",
                "ExecStart": f"{CANONICAL_STACK}/scripts/restore-test.sh",
            },
        }
        expected_working_directories = {
            "edsys-automation-compose.service": CANONICAL_STACK,
        }
        units = {path.name: path for path in (ROOT / "systemd").glob("*.service")}
        self.assertEqual(set(units), set(expected_commands))

        for name, expected in expected_commands.items():
            unit = units[name].read_text(encoding="utf-8")
            directives: dict[str, str] = {}
            for line in unit.splitlines():
                if line.startswith(("ExecStartPre=", "ExecStart=", "ExecStop=", "ExecReload=")):
                    key, value = line.split("=", 1)
                    self.assertNotIn(key, directives, f"duplicate {key} in {name}")
                    directives[key] = value
            with self.subTest(unit=name):
                self.assertEqual(directives, expected)
                guard_line = f"ExecStartPre={expected['ExecStartPre']}"
                start_line = f"ExecStart={expected['ExecStart']}"
                self.assertLess(
                    unit.index(guard_line),
                    unit.index(start_line),
                    f"{name} must guard its source before root execution",
                )
                user = re.search(r"(?m)^User=(.+)$", unit)
                self.assertEqual(user.group(1) if user else "root", "root")

            working = re.findall(r"(?m)^WorkingDirectory=(.+)$", unit)
            expected_working = expected_working_directories.get(name)
            self.assertEqual(working, [expected_working] if expected_working else [])

        # Bootstrap installs and enables only the reviewed tracked unit; an
        # inline duplicate could drift from this exact contract.
        bootstrap = text("scripts/bootstrap-guest.sh")
        self.assertNotIn("cat >/etc/systemd/system/edsys-automation-firewall.service", bootstrap)
        self.assertIn('for unit in "$stack_dir"/systemd/*', bootstrap)
        self.assertLess(
            bootstrap.index('for unit in "$stack_dir"/systemd/*'),
            bootstrap.index("systemctl enable --now edsys-automation-firewall.service"),
        )

        guard = text("scripts/source-guard.sh")
        guarded_expressions = {
            "/usr/bin/docker": "require_safe_executable /usr/bin/docker",
            "/usr/local/sbin/edsys-automation-firewall": (
                "require_safe_executable /usr/local/sbin/edsys-automation-firewall"
            ),
            INSTALLED_SOURCE_GUARD: f"require_safe_executable {INSTALLED_SOURCE_GUARD}",
            f"{CANONICAL_STACK}/scripts/backup.sh": (
                'require_safe_executable "$expected_stack/scripts/backup.sh"'
            ),
            f"{CANONICAL_STACK}/scripts/restore-test.sh": (
                'require_safe_executable "$expected_stack/scripts/restore-test.sh"'
            ),
        }
        for executable, expression in guarded_expressions.items():
            with self.subTest(guarded_executable=executable):
                self.assertIn(expression, guard)

        # Every tracked source path named by systemd must exist locally; the
        # exact expected map above makes any new root path an explicit review.
        for expected in expected_commands.values():
            for directive in expected.values():
                executable = directive.split()[0]
                if executable.startswith(f"{CANONICAL_STACK}/"):
                    relative = executable.removeprefix(f"{CANONICAL_STACK}/")
                    with self.subTest(tracked_systemd_path=relative):
                        self.assertTrue((ROOT / relative).is_file(), f"missing {relative}")

    def test_backup_and_restore_are_application_aware_and_isolated(self) -> None:
        backup = text("scripts/backup.sh")
        restore = text("scripts/restore-test.sh")
        for contract in (
            "docker compose kill -s USR1 mosquitto",
            'if length == 1 and (.[0] | type) == "array" then .[0] else . end',
            "sort_by(.Service)",
            "bundle create /tmp/edcore-automation.bundle --all",
            "node-red cat /tmp/edcore-automation.bundle",
            "init.defaultBranch=main init --bare",
            "bundle verify",
            "influx backup",
            "automation_runtime.backup",
            "automation-runtime cat /tmp/automation-runtime.sqlite3",
            "PRAGMA integrity_check",
            "SHA256SUMS",
        ):
            with self.subTest(backup_contract=contract):
                self.assertIn(contract, backup)
        self.assertIn('docker network create --internal "$network"', restore)
        self.assertIn('install -o root -g root -m 0444', restore)
        self.assertIn('$test_dir/mosquitto-config/mosquitto.conf:', restore)
        self.assertNotRegex(restore, r"(?m)^\s*(?:-p|--publish)\s")
        self.assertIn("sha256sum -c SHA256SUMS", restore)
        self.assertIn("init.defaultBranch=main init --bare", restore)
        self.assertIn("bundle verify", restore)
        self.assertIn("influx restore", restore)
        self.assertIn('export INFLUX_TOKEN="$(cat /run/secrets/admin_token)"', restore)
        self.assertIn("DOCKER_INFLUXDB_INIT_MODE=setup", restore)
        self.assertIn("DOCKER_INFLUXDB_INIT_ADMIN_TOKEN_FILE=/run/secrets/admin_token", restore)
        self.assertIn("PRAGMA integrity_check", restore)
        self.assertIn("--retained-only", restore)
        self.assertIn("edsys/v1/command/ha/#", restore)
        self.assertIn("retained_rc -eq 27", restore)


class SecretCustodyContractTestCase(unittest.TestCase):
    def test_generate_secrets_has_fixed_host_path_shape_and_scoped_modes(self) -> None:
        source = text("scripts/generate-secrets.sh")
        self.assertIn("readonly secret_root=/etc/edsys-secrets/edcore-automation", source)
        self.assertIn('[[ ${EUID} -eq 0 ]]', source)
        self.assertIn('[[ $# -eq 0 ]]', source)
        self.assertIn('[[ $(hostname -s) == edcore-automation ]]', source)
        self.assertIn("edsys-automation-source-guard --coherent", source)
        self.assertIn("This script accepts no path overrides.", source)
        self.assertIn(r"\( -type l -o \! -type d \! -type f \)", source)
        self.assertIn("-type f -links +1", source)

        for contract in (
            'install -d -o root -g root -m 0750',
            'chmod 0400 "$ca_dir/ca.key"',
            'chmod 0444 "$ca_dir/ca.crt"',
            "key_mode=0440",
            "[[ $custody == external ]] && key_mode=0400",
            'chmod 0444 "$cert"',
            'chmod 0440 "$secret_root/node-red/admin_password_hash"',
            'install -o root -g root -m 0440 /dev/null',
            'chmod 0600 "$ca_dir/ca.srl"',
        ):
            with self.subTest(permission_contract=contract):
                self.assertIn(contract, source)
        self.assertIn(
            "for identity in mqtt-health nodered automation-runtime telegraf event-replay command-audit",
            source,
        )
        self.assertIn("for identity in homeassistant frigate edsys-edge-livingroom", source)

    def test_age_escrow_creator_and_9950x_verifier_are_fail_closed(self) -> None:
        create = text("scripts/create-secret-escrow.sh")
        verify = text("scripts/verify-secret-escrow.sh")
        for contract in (
            "readonly secret_root=/etc/edsys-secrets/edcore-automation",
            "readonly recipient_file=$escrow_config/edcore-automation-recipient.txt",
            "readonly escrow_root=/var/backups/edcore-automation-secret-escrow",
            '[[ $(hostname -s) == edcore-automation ]]',
            '[[ $# -eq 1 && $1 == --create ]]',
            "0:0:644",
            "age1[0-9a-z]",
            "pki/clients/edsys-edge-livingroom.key",
            'age -r "${recipients[0]}" -o "$temporary"',
            "edcore-automation-secrets-$run_id.tar.age",
            'chmod 0600 "$temporary"',
            'mv "$temporary" "$final"',
            'ln -sfn "$(basename "$final")" "$escrow_root/current"',
        ):
            with self.subTest(creator_contract=contract):
                self.assertIn(contract, create)

        for contract in (
            "readonly identity=/etc/edsys-secrets/edcore-automation-escrow/identity.txt",
            "readonly installed_path=/usr/local/sbin/edsys-automation-verify-secret-escrow",
            '$(readlink -e -- "${BASH_SOURCE[0]}") == "$installed_path"',
            '$(stat -c \'%u:%g:%a:%h\' "$installed_path") == 0:0:755:1',
            '[[ $(stat -c \'%u:%g\' "$current") == 0:0 ]]',
            '(( (8#$mode & 8#022) == 0 ))',
            '[[ $(hostname -s) != edcore-automation ]]',
            "$archive == /*",
            '$(stat -c \'%u:%g:%a:%h\' "$archive") == 0:0:600:1',
            '$(stat -c \'%u:%g:%a:%h\' "$identity") == 0:0:600:1',
            '[[ $(readlink -e -- "$protected_path") == "$protected_path" ]]',
            "mktemp -d /dev/shm/edcore-automation-escrow.",
            'age -d -i "$identity" "$archive"',
            "pki/ca/ca.key pki/ca/ca.crt pki/ca/ca.srl",
            "pki/clients/homeassistant.key pki/clients/homeassistant.crt",
            "pki/clients/frigate.key pki/clients/frigate.crt",
            "pki/clients/edsys-edge-livingroom.key pki/clients/edsys-edge-livingroom.crt",
            "pki/clients/mqtt-health.key pki/clients/mqtt-health.crt",
            "pki/clients/nodered.key pki/clients/nodered.crt",
            "pki/clients/automation-runtime.key pki/clients/automation-runtime.crt",
            "pki/clients/telegraf.key pki/clients/telegraf.crt",
            "pki/clients/event-replay.key pki/clients/event-replay.crt",
            "pki/clients/command-audit.key pki/clients/command-audit.crt",
            'for certificate in "$restored"/pki/servers/*.crt "$restored"/pki/clients/*.crt',
            'key_hash=$(openssl pkey -in "$private_key" -pubout',
            'certificate_hash=$(openssl x509 -in "$certificate" -pubkey -noout',
            '[[ $key_hash == "$certificate_hash" ]]',
            '"schema": "edsys.edcore-automation.secret-escrow-acceptance.v1"',
            '"archive_sha256": sys.argv[2]',
            '"tested_on": socket.gethostname().split(".", 1)[0]',
        ):
            with self.subTest(verifier_contract=contract):
                self.assertIn(contract, verify)

    def test_offhost_verifier_decrypts_once_then_uses_installed_safe_archive_helper(self) -> None:
        verify = text("scripts/verify-secret-escrow.sh")
        helper = text("scripts/secret_escrow_archive.py")
        for contract in (
            "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
            "export PATH",
            "unset PYTHONHOME PYTHONPATH",
            "readonly archive_helper=/usr/local/libexec/edsys-automation-secret-escrow-archive.py",
            "readonly max_archive_bytes=$((32 * 1024 * 1024))",
            "0:0:600:1",
            '$(stat -c \'%u:%g:%a:%h\' "$archive_helper") == 0:0:644:1',
            'for protected_path in "$archive" "$identity" "$archive_helper"',
            "plaintext_tar=$test_root/archive.tar",
            "extract_root=$test_root/extracted",
            "ulimit -f 65536",
            ') >"$plaintext_tar"',
            '$(stat -c \'%u:%g:%a:%h\' "$plaintext_tar") == 0:0:600:1',
            'python3 -I -B "$archive_helper" "$plaintext_tar" "$extract_root"',
            'python3 -I -B - "$(basename "$archive")" "$archive_hash"',
            "restored=$extract_root/edcore-automation",
        ):
            with self.subTest(verifier_archive_contract=contract):
                self.assertIn(contract, verify)
        self.assertEqual(verify.count('age -d -i "$identity" "$archive"'), 1)
        self.assertNotRegex(verify, r"(?m)^\s*tar\s")
        self.assertLess(verify.index("age -d -i"), verify.index('python3 -I -B "$archive_helper"'))
        self.assertLess(verify.index('python3 -I -B "$archive_helper"'), verify.index("restored=$extract_root"))
        self.assertEqual(verify.count("python3 -I -B"), 2)

        for contract in (
            "MAX_ARCHIVE_BYTES = 32 * 1024 * 1024",
            "MAX_MEMBERS = 512",
            "MAX_MEMBER_BYTES = 4 * 1024 * 1024",
            "MAX_TOTAL_FILE_BYTES = 16 * 1024 * 1024",
            "def inspect_archive(path: Path)",
            "def extract_accepted(path: Path, destination: Path, members:",
            "members = inspect_archive(path)",
            "extract_accepted(path, destination, members)",
            "archive has a duplicate normalized path",
            "global PAX metadata is forbidden",
            "per-member PAX metadata is forbidden",
            'getattr(info, "sparse", None)',
            "archive contains a link or special member",
            "plaintext tar has non-zero trailing data",
            "os.O_EXCL",
            "os.O_NOFOLLOW",
        ):
            with self.subTest(helper_archive_contract=contract):
                self.assertIn(contract, helper)
        self.assertLess(helper.index("members = inspect_archive(path)"), helper.index("extract_accepted(path, destination, members)"))

    def test_synthetic_edge_ingestion_is_sanitized_bounded_and_evidenced(self) -> None:
        source = text("scripts/verify-edge-ingestion.sh")
        for contract in (
            "readonly stack_dir=/srv/edsys/edsys-infrastructure/docker/edcore-automation",
            "readonly acceptance=$evidence_root/edsys-edge-livingroom-ingestion.json",
            "readonly broker_network=edsys-edcore-automation-broker",
            "readonly data_network=edsys-edcore-automation-data",
            "readonly source_topic=edsys/v1/telemetry/environment/edge-livingroom/synthetic",
            "readonly topic_pseudonym_input=edge-livingroom/synthetic",
            "readonly payload_pseudonym_input=edge-livingroom",
            '[[ $# -eq 1 && $1 == --accept ]]',
            "/usr/local/sbin/edsys-automation-source-guard --runtime",
            'source_digest=$(printf \'%s\' "$topic_pseudonym_input"',
            'payload_source_digest=$(printf \'%s\' "$payload_pseudonym_input"',
            '$source_digest != "$payload_source_digest"',
            'readonly sanitized_topic="telemetry/environment/source-$source_digest"',
            'readonly sanitized_payload_source="source-$payload_source_digest"',
            'readonly replay_topic="edsys/test/v1/replay/$run_id/$sanitized_topic"',
            'payload=$(python3 - "$synthetic_value" "$payload_pseudonym_input"',
            "event-harness record",
            'assert len(environment) == 1',
            'assert event["topic"] == expected_topic',
            'event-harness - "$trace_path" "$sanitized_topic" "$sanitized_payload_source"',
            "expected_payload_source = sys.argv[3]",
            "raw_identity = sys.argv[5]",
            "assert raw_identity not in decoded",
            'assert expected_payload_source != expected_topic.rsplit("/", 1)[1]',
            'assert event["payload"]["source"] == expected_payload_source',
            "hashlib.sha256(raw).hexdigest()",
            "event-harness replay",
            'jq -e --arg source "$sanitized_payload_source"',
            'mqtt_base command-audit "edge-audit-$mqtt_id_suffix" mosquitto_sub',
            '[[ $command_rc -eq 27 && ! -s "$scratch/command.json" ]]',
            'r._measurement == "selected_telemetry"',
            "r.value_count == 1",
            "r.value_min == $synthetic_value",
            "r.value_max == $synthetic_value",
            "r.value_mean == $synthetic_value",
            '"schema": "edsys.edcore-automation.synthetic-ingestion-acceptance.v1"',
            '"source_topic": sys.argv[3]',
            '"sanitized_topic": sys.argv[4]',
            '"trace_sha256": sys.argv[5]',
            'chmod 0600 "$temporary"',
            'mv "$temporary" "$acceptance"',
        ):
            with self.subTest(edge_ingestion_contract=contract):
                self.assertIn(contract, source)

        edge_publish = 'mqtt_base edsys-edge-livingroom "edge-pub-$mqtt_id_suffix" mosquitto_pub'
        self.assertLess(source.index("event-harness record"), source.index(edge_publish))
        self.assertLess(source.index(edge_publish), source.index("event-harness replay"))
        self.assertLess(source.index("event-harness replay"), source.index("Synthetic selected telemetry was not proven"))
        self.assertNotIn(
            'assert event["payload"]["source"] == expected_topic.rsplit("/", 1)[1]',
            source,
        )
        self.assertNotIn('--arg source "source-$source_digest"', source)

    def test_edge_flux_success_marker_preserves_group_key_columns(self) -> None:
        source = text("scripts/verify-edge-ingestion.sh")
        query_match = re.search(
            r"(?ms)^flux_query=\$\(cat <<EOF\n(?P<query>.*?)^EOF\n\)$",
            source,
        )
        self.assertIsNotNone(query_match)
        query = query_match.group("query")
        group = '|> group(columns: ["_measurement", "host"])'
        record_update = (
            '|> map(fn: (r) => ({r with _value: "edsys-edge-ingestion-passed"}))'
        )
        old_object_replacement = (
            '|> map(fn: (r) => ({_value: "edsys-edge-ingestion-passed"}))'
        )
        self.assertIn(group, query)
        self.assertIn(record_update, query)
        self.assertNotIn(old_object_replacement, query)
        self.assertNotIn(old_object_replacement, source)
        self.assertLess(query.index(group), query.index(record_update))
        self.assertIn(
            "grep -Eq '(^|,)edsys-edge-ingestion-passed(,|$)'",
            source,
        )

        # Flux record-update semantics retain the grouped columns needed to
        # emit a data row. Replacing the record with {_value: ...} loses both
        # group keys and can reduce the raw result to a header-only table.
        grouped_record = {
            "_measurement": "selected_telemetry",
            "host": "edcore-automation",
            "_value": 1.0,
        }
        updated_record = {
            **grouped_record,
            "_value": "edsys-edge-ingestion-passed",
        }
        self.assertEqual(updated_record["_measurement"], "selected_telemetry")
        self.assertEqual(updated_record["host"], "edcore-automation")
        self.assertEqual(updated_record["_value"], "edsys-edge-ingestion-passed")
        self.assertNotIn("_measurement", {"_value": "edsys-edge-ingestion-passed"})
        self.assertNotIn("host", {"_value": "edsys-edge-ingestion-passed"})

    def test_edge_flux_csv_gate_normalizes_only_cr_and_preserves_pipefail(self) -> None:
        source = text("scripts/verify-edge-ingestion.sh")
        exact_gate = (
            'if influx_query "$flux_query" 2>"$scratch/influx.err" | \\\n'
            "    tr -d '\\r' | \\\n"
            "    grep -Eq '(^|,)edsys-edge-ingestion-passed(,|$)'; then"
        )
        self.assertIn("set -Eeuo pipefail", source)
        self.assertIn(exact_gate, source)
        gate_position = source.index(exact_gate)
        query_position = source.index('influx_query "$flux_query"', gate_position)
        normalizer_position = source.index("tr -d '\\r'", query_position)
        marker_position = source.index(
            "grep -Eq '(^|,)edsys-edge-ingestion-passed(,|$)'",
            normalizer_position,
        )
        self.assertLess(query_position, normalizer_position)
        self.assertLess(normalizer_position, marker_position)
        self.assertEqual(source.count("tr -d '\\r'"), 1)
        self.assertEqual(
            re.findall(r"\btr\s+-d\s+(?:'[^']*'|\"[^\"]*\")", exact_gate),
            ["tr -d '\\r'"],
        )
        for broad_normalization in (
            "tr -d '[:space:]'",
            "tr -d '[:blank:]'",
            "tr -d '\\n'",
            "tr -d '\\r\\n'",
            "xargs",
        ):
            with self.subTest(broad_normalization=broad_normalization):
                self.assertNotIn(broad_normalization, exact_gate)

        filter_pipeline = (
            "cat | tr -d '\\r' | "
            "grep -Eq '(^|,)edsys-edge-ingestion-passed(,|$)'"
        )

        def marker_result(payload: bytes) -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                ["bash", "-o", "pipefail", "-c", filter_pipeline],
                input=payload,
                capture_output=True,
                check=False,
            )

        for payload in (
            b"result,table,_value\n,,edsys-edge-ingestion-passed\n",
            b"result,table,_value\r\n,,edsys-edge-ingestion-passed\r\n",
        ):
            with self.subTest(accepted_payload=payload):
                self.assertEqual(marker_result(payload).returncode, 0)
        for payload in (
            b"result,table,_value\n",
            b"result,table,_value\r\n",
            b",,edsys-edge-ingestion-pass\n",
            b",,edsys-edge-ingestion-passed-extra\n",
            b',,"edsys-edge-ingestion-passed"\n',
            b",, edsys-edge-ingestion-passed \n",
            b",,unrelated\n",
        ):
            with self.subTest(rejected_payload=payload):
                self.assertNotEqual(marker_result(payload).returncode, 0)

        upstream_failure = subprocess.run(
            [
                "bash",
                "-o",
                "pipefail",
                "-c",
                "producer() { "
                "printf '%s\\r\\n' ',,edsys-edge-ingestion-passed'; return 42; }\n"
                "producer | tr -d '\\r' | "
                "grep -Eq '(^|,)edsys-edge-ingestion-passed(,|$)'",
            ],
            capture_output=True,
            check=False,
        )
        self.assertEqual(upstream_failure.returncode, 42)

    def test_external_delivery_disposition_and_online_key_finalization_are_exact(self) -> None:
        delivery = text("scripts/record-client-delivery.sh")
        disposition = text("scripts/record-client-disposition.sh")
        finalize = text("scripts/finalize-online-keys.sh")

        self.assertIn('[[ $# -eq 2 && $2 == --accepted ]]', delivery)
        self.assertIn("homeassistant|frigate", delivery)
        self.assertNotIn("edsys-edge-livingroom|", delivery)
        self.assertIn("edsys.edcore-automation.client-delivery.v1", delivery)
        self.assertIn('key_hash=$(openssl pkey -in "$key" -pubout', delivery)
        self.assertIn('cert_key_hash=$(openssl x509 -in "$cert" -pubkey -noout', delivery)
        self.assertIn('[[ $key_hash == "$cert_key_hash" ]]', delivery)
        self.assertIn('chmod 0600 "$temporary"', delivery)

        self.assertIn(
            '[[ $# -eq 2 && $1 == edsys-edge-livingroom && $2 == --unused ]]',
            disposition,
        )
        self.assertIn(
            "readonly ingestion_acceptance=/etc/edsys-escrow/client-disposition/edsys-edge-livingroom-ingestion.json",
            disposition,
        )
        self.assertIn("edsys.edcore-automation.synthetic-ingestion-acceptance.v1", disposition)
        self.assertIn('"ingestion_acceptance_sha256": sys.argv[1]', disposition)
        self.assertIn("edsys.edcore-automation.client-disposition.v1", disposition)
        self.assertIn('"disposition": "unused-not-delivered"', disposition)
        self.assertIn('chmod 0600 "$temporary"', disposition)

        self.assertIn("edcore-automation-accepted.json", finalize)
        self.assertIn("for identity in homeassistant frigate", finalize)
        self.assertIn("client-disposition/edsys-edge-livingroom.json", finalize)
        self.assertIn("client-disposition/edsys-edge-livingroom-ingestion.json", finalize)
        self.assertIn("ingestion_acceptance_sha256", finalize)
        self.assertIn("edsys.edcore-automation.synthetic-ingestion-acceptance.v1", finalize)
        self.assertIn("edsys.edcore-automation.client-delivery.v1", finalize)
        self.assertIn("edsys.edcore-automation.client-disposition.v1", finalize)
        for key in (
            '"$secret_root/pki/ca/ca.key"',
            '"$secret_root/pki/clients/homeassistant.key"',
            '"$secret_root/pki/clients/frigate.key"',
            '"$secret_root/pki/clients/edsys-edge-livingroom.key"',
        ):
            with self.subTest(removed_online_key=key):
                self.assertIn(key, finalize)
        self.assertIn('finalized=$escrow_config/online-keys-finalized.json', finalize)
        self.assertIn(
            '"schema": "edsys.edcore-automation.online-key-finalization.v1"',
            finalize,
        )
        self.assertIn('chmod 0600 "$finalized.new"', finalize)
        self.assertIn('mv "$finalized.new" "$finalized"', finalize)

    def test_accepted_age_ciphertext_is_carried_in_normal_backup(self) -> None:
        backup = text("scripts/backup.sh")
        for contract in (
            "readonly secret_escrow_root=/var/backups/edcore-automation-secret-escrow",
            "readonly secret_escrow_acceptance=/etc/edsys-escrow/edcore-automation-accepted.json",
            "0:0:600",
            "^age-encryption.org/v1",
            "edsys.edcore-automation.secret-escrow-acceptance.v1",
            '"$staging/influxdb-backup" \\',
            '"$staging/secret-escrow" "$staging/custody-evidence"',
            'cp "$secret_escrow" "$staging/secret-escrow/$(basename "$secret_escrow")"',
            'chmod 0600 "$staging/secret-escrow/$(basename "$secret_escrow")"',
            'cp "$secret_escrow_acceptance" "$staging/secret-escrow/ACCEPTANCE.json"',
            'chmod 0600 "$staging/secret-escrow/ACCEPTANCE.json"',
            "/etc/edsys-escrow/client-delivery/homeassistant.json",
            "/etc/edsys-escrow/client-delivery/frigate.json",
            "/etc/edsys-escrow/client-disposition/edsys-edge-livingroom-ingestion.json",
            "/etc/edsys-escrow/client-disposition/edsys-edge-livingroom.json",
            "/etc/edsys-escrow/online-keys-finalized.json",
            'cp "$evidence" "$staging/custody-evidence/$(basename "$evidence")"',
            'chmod 0600 "$staging"/custody-evidence/*',
        ):
            with self.subTest(backup_escrow_contract=contract):
                self.assertIn(contract, backup)
        self.assertNotIn("age -d", backup)
        self.assertNotIn('cp -a "$secret_root"', backup)


class OperationalSafetyContractTestCase(unittest.TestCase):
    def test_healthchecks_urls_use_private_0600_env_and_curl_stdin(self) -> None:
        units = {
            "scripts/backup.sh": (
                "systemd/edsys-automation-backup.service",
                "/etc/edsys-secrets/edcore-automation/healthchecks/backup.env",
            ),
            "scripts/restore-test.sh": (
                "systemd/edsys-automation-restore-test.service",
                "/etc/edsys-secrets/edcore-automation/healthchecks/restore-test.env",
            ),
        }
        for script, (unit, environment_file) in units.items():
            source = text(script)
            with self.subTest(script=script):
                self.assertIn(f"readonly healthchecks_env={environment_file}", source)
                self.assertIn("0:0:600", source)
                self.assertIn("env -u HC_PING_URL curl --config -", source)
                self.assertIn('url = "${url}${suffix', source)
                self.assertNotRegex(source, r"curl[^\n]*\$\{?HC_PING_URL")
                self.assertIn(f"EnvironmentFile=-{environment_file}", text(unit))

    def test_firewall_replacement_is_atomic_and_invalid_candidate_preserves_active_rules(self) -> None:
        firewall = text("scripts/firewall-apply.sh")
        verify = text("scripts/verify.sh")
        self.assertIn("transaction=$(mktemp /run/edsys-automation-firewall.", firewall)
        self.assertIn("delete table inet edsys_automation_filter", firewall)
        self.assertIn('cat -- "$config" >>"$transaction"', firewall)
        self.assertIn('nft -c -f "$transaction"', firewall)
        self.assertIn('nft -f "$transaction"', firewall)
        self.assertLess(
            firewall.index('nft -c -f "$transaction"'),
            firewall.index('nft -f "$transaction"'),
        )
        self.assertNotRegex(firewall, r"(?m)^nft delete table")

        self.assertIn("firewall_before=$(nft -j list table", verify)
        self.assertIn("this is deliberately invalid nft syntax", verify)
        self.assertIn('edsys-automation-firewall --candidate "$scratch/invalid-firewall.nft"', verify)
        self.assertIn("firewall_after=$(nft -j list table", verify)
        self.assertIn('[[ $firewall_after == "$firewall_before" ]]', verify)


if __name__ == "__main__":
    unittest.main()
