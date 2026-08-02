"""Create bounded ntfy event rules without embedding credentials."""

from django.contrib.contenttypes.models import ContentType

from extras.models import EventRule, Webhook


NTFY_URL = "http://192.168.50.50:3015/edsys-netbox"


def ensure_webhook(name, *, priority, tags):
    webhook, _ = Webhook.objects.update_or_create(
        name=name,
        defaults={
            "description": "Bounded EdSys NetBox notification; contains no credentials or private inventory payload.",
            "payload_url": NTFY_URL,
            "http_method": "POST",
            "http_content_type": "text/plain",
            "additional_headers": f"Title: EdSys NetBox\nPriority: {priority}\nTags: {tags}",
            "body_template": "NetBox {{ event }}: {{ model }} {{ data.display | default(data.name) | default(data.id) }}",
            "secret": "",
            "ssl_verification": True,
        },
    )
    webhook.full_clean()
    webhook.save()
    return webhook


def ensure_rule(name, webhook, *, event_types, object_types, conditions=None, description=""):
    webhook_type = ContentType.objects.get_for_model(Webhook)
    rule, _ = EventRule.objects.update_or_create(
        name=name,
        defaults={
            "description": description,
            "event_types": event_types,
            "enabled": True,
            "conditions": conditions,
            "action_type": "webhook",
            "action_object_type": webhook_type,
            "action_object_id": webhook.pk,
            "action_data": None,
            "comments": "Notification only. This rule never changes infrastructure configuration.",
        },
    )
    rule.full_clean()
    rule.save()
    rule.object_types.set(object_types)
    return rule


failed = ensure_webhook("EdSys ntfy - failed NetBox jobs", priority="high", tags="warning")
critical = ensure_webhook("EdSys ntfy - critical inventory changes", priority="default", tags="gear")

job_type = ContentType.objects.get(app_label="core", model="job")
ensure_rule(
    "Notify failed NetBox jobs",
    failed,
    event_types=["job_failed", "job_errored"],
    object_types=[job_type],
    description="Notify only when a NetBox background job fails or errors.",
)

critical_types = [
    ContentType.objects.get(app_label=app, model=model)
    for app, model in (
        ("dcim", "device"),
        ("virtualization", "virtualmachine"),
        ("ipam", "prefix"),
        ("ipam", "ipaddress"),
        ("ipam", "service"),
    )
]
ensure_rule(
    "Notify critical infrastructure changes",
    critical,
    event_types=["object_created", "object_updated", "object_deleted"],
    object_types=critical_types,
    conditions={"attr": "custom_fields.criticality", "value": ["critical", "high"], "op": "in"},
    description="Notify bounded changes only when the reviewed criticality field is critical or high.",
)

print("Bounded NetBox ntfy webhooks and event rules are enabled; no credentials were used.")
