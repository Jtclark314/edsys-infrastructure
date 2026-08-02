"""EdSys production-only NetBox settings not exposed by netbox-docker env helpers."""

CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
# This policy is scoped below the private netbox.edsys.local name only.
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# A private .local name cannot and should not be submitted to the public HSTS
# preload list; the corresponding deploy-check warning is intentionally retained.
SECURE_HSTS_PRELOAD = False
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# Never exempt any model from normal view-permission checks.
EXEMPT_VIEW_PERMISSIONS = []

# Prevent accidental plugin enablement without a reviewed image rebuild.
PLUGINS = []
PLUGINS_CONFIG = {}

# Docker's local logging driver bounds stdout/stderr retention. NetBox's
# maintained default logging configuration is retained rather than shadowed.
