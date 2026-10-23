from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

User = get_user_model()


class WebSocketOriginTests(SimpleTestCase):
    """
    Browsers attach the session cookie to cross-site WebSocket handshakes, so
    the router must refuse any Origin that is not one of ALLOWED_HOSTS.
    """

    async def _connect(self, origin):
        # asgiref's communicator, not channels.testing (which needs daphne).
        from asgiref.testing import ApplicationCommunicator

        from Icarus.asgi import application

        communicator = ApplicationCommunicator(application, {
            "type": "websocket",
            "path": "/ws/kalapila/page/1/comments/",
            "query_string": b"",
            "headers": [(b"origin", origin), (b"host", b"testserver")],
            "subprotocols": [],
        })
        await communicator.send_input({"type": "websocket.connect"})
        reply = await communicator.receive_output(timeout=2)
        await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
        await communicator.wait(timeout=2)
        return reply["type"] == "websocket.accept"

    async def test_foreign_origin_is_rejected(self):
        self.assertFalse(await self._connect(b"https://evil.example"))

    async def test_own_origin_is_accepted(self):
        self.assertTrue(await self._connect(b"http://testserver"))


class MessageEscapingTests(TestCase):
    """base.html rendered every message with |safe; messages can carry user input."""

    def test_messages_are_escaped_unless_marked_safe(self):
        from django.contrib import messages
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib.sessions.backends.db import SessionStore
        from django.template.loader import render_to_string
        from django.test import RequestFactory
        from django.utils.html import format_html

        request = RequestFactory().get("/")
        request.user = User.objects.create_user(phone_number="+918800330001", name="R")
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        messages.error(request, "<img src=x onerror=alert(1)>")
        messages.info(request, format_html("<strong>{}</strong>", "trusted markup"))

        html = render_to_string("base.html", request=request)

        self.assertNotIn("<img src=x onerror=alert(1)>", html)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
        self.assertIn("<strong>trusted markup</strong>", html)


class PermissionDeniedPageTests(TestCase):
    def test_403_page_offers_plans_to_signed_in_readers(self):
        from django.urls import reverse

        self.client.force_login(User.objects.create_user(phone_number="+918800330002", name="R"))
        response = self.client.get(reverse("the_librarian:viewer", args=[1]))
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "403.html")
        self.assertContains(response, reverse("reader_profile"), status_code=403)


class HealthCheckTests(TestCase):
    """/healthz is what Docker probes; it must not depend on the Host header."""

    def test_ok_for_any_host(self):
        response = self.client.get("/healthz", HTTP_HOST="127.0.0.1:8000")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")
        response = self.client.get("/healthz", HTTP_HOST="not-an-allowed-host.example")
        self.assertEqual(response.status_code, 200)

    def test_503_when_the_database_is_down(self):
        from unittest.mock import patch

        with patch("Icarus.health.connection.cursor", side_effect=RuntimeError("db down")):
            response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 503)

    def test_other_paths_are_untouched(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.content, b"ok")


class WorkflowTests(SimpleTestCase):
    """
    Every push to master deploys (deploy.yml), as it always has; ci.yml only
    tests. An earlier change replaced the deploy-on-push workflow with a
    manual one, which would have quietly stopped deploys.
    """

    @staticmethod
    def _workflow(name):
        from pathlib import Path

        import yaml
        from django.conf import settings

        data = yaml.safe_load((Path(settings.BASE_DIR) / ".github" / "workflows" / name).read_text())
        data["on"] = data.pop(True, None) or data.get("on")  # YAML 1.1 reads a bare `on:` as True
        return data

    def test_every_push_to_master_deploys(self):
        workflow = self._workflow("deploy.yml")
        branches = workflow["on"]["push"]["branches"]
        self.assertIn("master", [branches] if isinstance(branches, str) else branches)
        job = workflow["jobs"]["deploy"]
        self.assertEqual(job["runs-on"], "self-hosted")
        self.assertIn("./deploy.sh", [step.get("run") for step in job["steps"]])

    def test_ci_tests_master_and_development_and_never_deploys(self):
        workflow = self._workflow("ci.yml")
        for event in ("push", "pull_request"):
            with self.subTest(event=event):
                self.assertEqual(set(workflow["on"][event]["branches"]), {"development", "master"})
        runs = [step.get("run") or "" for job in workflow["jobs"].values() for step in job.get("steps", [])]
        self.assertFalse([r for r in runs if "deploy.sh" in r])


class TorchRequirementTests(SimpleTestCase):
    """
    There is no "+cpu" torch build for macOS: with one unconditional pin, pip
    rejected the whole requirements file on a Mac, so nothing was upgraded
    and archive search failed for want of sentence-transformers.
    """

    def _torch_pins(self, platform):
        from pathlib import Path

        from django.conf import settings
        from packaging.requirements import InvalidRequirement, Requirement

        pins = []
        for line in (Path(settings.BASE_DIR) / "requirements.txt").read_text().splitlines():
            line = line.split(" #")[0].strip()
            if not line.startswith("torch"):
                continue
            try:
                req = Requirement(line)
            except InvalidRequirement:
                continue
            env = {"sys_platform": platform, "platform_system": {"linux": "Linux", "darwin": "Darwin", "win32": "Windows"}[platform]}
            if req.name == "torch" and (req.marker is None or req.marker.evaluate(env)):
                pins.append(str(req.specifier))
        return pins

    def test_the_server_keeps_the_cpu_build(self):
        self.assertEqual(self._torch_pins("linux"), ["==2.7.0+cpu"])

    def test_a_mac_gets_an_installable_build(self):
        self.assertEqual(self._torch_pins("darwin"), ["==2.7.0"])

    def test_windows_keeps_the_cpu_build(self):
        self.assertEqual(self._torch_pins("win32"), ["==2.7.0+cpu"])
