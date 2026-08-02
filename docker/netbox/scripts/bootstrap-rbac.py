"""Idempotently create EdSys NetBox roles, automation users, and v2 tokens."""

from pathlib import Path

from django.contrib.contenttypes.models import ContentType

from users.models import Group, ObjectPermission, Token, User
from netaddr import IPNetwork


INVENTORY_APPS = {
    "circuits",
    "dcim",
    "extras",
    "ipam",
    "tenancy",
    "virtualization",
    "wireless",
}
AUTOMATION_SOURCE = "192.168.50.50/32"


def inventory_content_types():
    excluded = {
        ("extras", "eventrule"),
        ("extras", "webhook"),
        ("extras", "script"),
    }
    return list(
        ContentType.objects.filter(app_label__in=INVENTORY_APPS)
        .exclude(app_label="extras", model__in=[model for app, model in excluded if app == "extras"])
        .order_by("app_label", "model")
    )


def ensure_group(name, description, actions):
    group, _ = Group.objects.update_or_create(name=name, defaults={"description": description})
    permission, _ = ObjectPermission.objects.update_or_create(
        name=f"{name} inventory access",
        defaults={
            "description": description,
            "enabled": True,
            "actions": actions,
            "constraints": None,
        },
    )
    permission.object_types.set(inventory_content_types())
    group.object_permissions.add(permission)
    return group


def ensure_user(username, group):
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"is_active": True, "email": f"{username}@edsys.local"},
    )
    user.is_active = True
    user.set_unusable_password()
    user.save()
    user.groups.set([group])
    return user


def parse_v2(path):
    value = Path(path).read_text(encoding="utf-8").strip()
    prefix = "Bearer nbt_"
    if not value.startswith(prefix) or "." not in value:
        raise RuntimeError(f"Invalid v2 bearer credential format in {path}")
    key, plaintext = value[len(prefix) :].split(".", 1)
    if len(key) != 12 or len(plaintext) != 64:
        raise RuntimeError(f"Invalid v2 bearer credential lengths in {path}")
    return key, plaintext


def ensure_token(user, path, write_enabled, description):
    key, plaintext = parse_v2(path)
    token = Token.objects.filter(key=key).first()
    if token is None:
        token = Token(
            version=2,
            user=user,
            key=key,
            token=plaintext,
            write_enabled=write_enabled,
            enabled=True,
            allowed_ips=[IPNetwork(AUTOMATION_SOURCE)],
            description=description,
        )
        token.full_clean()
        token.save()
    else:
        if not token.validate(plaintext):
            raise RuntimeError(f"Stored token key {key} does not match the supplied plaintext")
        token.user = user
        token.enabled = True
        token.write_enabled = write_enabled
        token.allowed_ips = [IPNetwork(AUTOMATION_SOURCE)]
        token.description = description
        token.full_clean()
        token.save()


editor = ensure_group(
    "inventory-editor",
    "Human inventory editors; deletion is intentionally excluded.",
    ["view", "add", "change"],
)
viewer = ensure_group("viewer", "Read-only infrastructure inventory access.", ["view"])
sync_group = ensure_group(
    "sync-automation",
    "Idempotent EdSys discovery upserts; deletion is intentionally excluded.",
    ["view", "add", "change"],
)
export_group = ensure_group("export-automation", "Read-only sanitized export access.", ["view"])

ensure_user("inventory-editor", editor)
ensure_user("inventory-viewer", viewer)
sync_user = ensure_user("edsys-sync", sync_group)
export_user = ensure_user("edsys-export", export_group)

ensure_token(
    sync_user,
    "/run/secrets/sync_api_bearer",
    True,
    "EdSys allowlisted synchronization token",
)
ensure_token(
    export_user,
    "/run/secrets/export_api_bearer",
    False,
    "EdSys allowlisted sanitized export token",
)

print("EdSys NetBox roles and scoped v2 tokens are ready; no credential values were displayed.")
