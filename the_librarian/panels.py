"""
Admin panels for The Librarian.
"""
import re

from django.conf import settings
from django.urls import reverse
from wagtail.admin.panels import Panel

# Where the worker code lives — shown in the setup instructions' clone step.
REPOSITORY_URL = "https://github.com/Appoox/Icarus.git"


def worker_model_name(model_name):
    """
    The name the worker should load the site's embedding model by.

    The site can load its model from a local folder named after the model,
    such as "models/intfloat/multilingual-e5-large/" (docker-compose.yml's
    model_volume). That path means nothing on the worker's computer, so this
    returns the Hugging Face name the folder is named after, which the worker
    downloads on its first run. Plain Hugging Face names pass through as-is.
    """
    name = model_name.rstrip("/")
    match = re.search(r"(?:^|/)models/(.+)$", name)
    return match.group(1) if match else name


def remote_worker_setup_context(request, *, on_worker_page):
    """
    Context for the_librarian/includes/remote_worker_setup.html, shared by
    the Remote Worker admin page and the Remote Ingestion Worker Settings
    page so both show the same setup steps.

    site_base_url is the address the worker should be pointed at: the site
    root, built the same way list_pending_for_worker builds the download
    links it hands the worker. embedding_model is passed to the worker so its
    vectors match this site's (submit_ingested_chunks rejects any other size).
    """
    return {
        "on_worker_page": on_worker_page,
        "site_base_url": request.build_absolute_uri("/").rstrip("/"),
        "worker_page_url": reverse("the_librarian:remote_worker_admin"),
        "repository_url": REPOSITORY_URL,
        "embedding_model": worker_model_name(settings.LIBRARIAN_EMBEDDING_MODEL),
    }


class RemoteWorkerSetupPanel(Panel):
    """Read-only panel with the remote worker setup instructions."""

    class BoundPanel(Panel.BoundPanel):
        template_name = "the_librarian/includes/remote_worker_setup.html"

        def get_context_data(self, parent_context=None):
            context = super().get_context_data(parent_context)
            context.update(remote_worker_setup_context(self.request, on_worker_page=False))
            return context
