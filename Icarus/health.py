"""
GET /healthz — liveness/readiness probe for Docker and Caddy.

Implemented as the first middleware, not a view, so it answers before
host validation (the container probes 127.0.0.1, which is not in
ALLOWED_HOSTS) and before sessions, auth or redirects run. It returns 200
"ok" only if the database answers; otherwise 503.
"""
import logging

from django.db import connection
from django.http import HttpResponse

logger = logging.getLogger(__name__)

HEALTH_PATH = "/healthz"


class HealthCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path != HEALTH_PATH:
            return self.get_response(request)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception:
            logger.exception("Health check: database unavailable")
            return HttpResponse("database unavailable", status=503, content_type="text/plain")
        return HttpResponse("ok", content_type="text/plain")
