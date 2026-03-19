"""
Wagtail hooks for The Librarian.

Adds an "Ingest Archive" button to the Wagtail admin dashboard
and a sidebar menu item for archive documents.
"""
from django.urls import reverse
from django.utils.safestring import mark_safe

from wagtail import hooks
from wagtail.admin.menu import MenuItem


# ── Sidebar menu item ────────────────────────────────────────────────────

@hooks.register("register_admin_menu_item")
def register_librarian_menu():
    return MenuItem(
        "Archive Search",
        reverse("the_librarian:search"),
        icon_name="search",
        order=900,
    )


# ── Dashboard "Ingest Archive" button panel ──────────────────────────────

@hooks.register("construct_homepage_panels")
def add_ingest_panel(request, panels):
    """Add an ingestion control panel to the Wagtail admin dashboard."""
    from the_librarian.models import ArchiveDocument, DocumentChunk

    doc_count = ArchiveDocument.objects.count()
    chunk_count = DocumentChunk.objects.count()
    ingest_url = reverse("the_librarian:trigger_ingestion")

    panel_html = f"""
    <section class="panel" id="librarian-ingest-panel">
        <header class="panel__header">
            <h2 class="panel__heading" style="display:flex;align-items:center;gap:8px;">
                <svg class="icon icon-doc-full" aria-hidden="true" style="width:1em;height:1em;">
                    <use href="#icon-doc-full"></use>
                </svg>
                The Librarian — Archive
            </h2>
        </header>
        <div class="panel__content" style="padding:1.5em;">
            <div style="display:flex;gap:2em;margin-bottom:1.2em;">
                <div>
                    <strong style="font-size:1.8em;">{doc_count}</strong>
                    <div style="color:var(--w-color-text-meta);">Documents ingested</div>
                </div>
                <div>
                    <strong style="font-size:1.8em;">{chunk_count}</strong>
                    <div style="color:var(--w-color-text-meta);">Text chunks stored</div>
                </div>
            </div>

            <div style="display:flex;gap:0.8em;flex-wrap:wrap;">
                <button type="button" id="btn-ingest-archive"
                        class="button button-small button--primary"
                        onclick="librarianIngest(false)"
                        style="display:inline-flex;align-items:center;gap:6px;">
                    <svg class="icon" aria-hidden="true" style="width:1em;height:1em;">
                        <use href="#icon-download"></use>
                    </svg>
                    Ingest New PDFs
                </button>
                <button type="button" id="btn-reingest-archive"
                        class="button button-small button--secondary"
                        onclick="librarianIngest(true)"
                        style="display:inline-flex;align-items:center;gap:6px;">
                    <svg class="icon" aria-hidden="true" style="width:1em;height:1em;">
                        <use href="#icon-rotate"></use>
                    </svg>
                    Re-ingest All
                </button>
            </div>

            <div id="ingest-status" style="margin-top:1em;display:none;">
                <div id="ingest-spinner" style="display:none;">
                    ⏳ Ingesting… this may take a while for large PDFs.
                </div>
                <div id="ingest-result" style="display:none;"></div>
            </div>
        </div>
    </section>

    <script>
    function librarianIngest(force) {{
        var statusDiv = document.getElementById('ingest-status');
        var spinner = document.getElementById('ingest-spinner');
        var resultDiv = document.getElementById('ingest-result');
        var btn = document.getElementById('btn-ingest-archive');
        var btnRe = document.getElementById('btn-reingest-archive');

        statusDiv.style.display = 'block';
        spinner.style.display = 'block';
        resultDiv.style.display = 'none';
        btn.disabled = true;
        btnRe.disabled = true;

        var formData = new FormData();
        formData.append('force', force ? 'true' : 'false');

        fetch('{ingest_url}', {{
            method: 'POST',
            headers: {{
                'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]')
                    ? document.querySelector('[name=csrfmiddlewaretoken]').value
                    : document.cookie.match(/csrftoken=([^;]+)/)?.[1] || ''
            }},
            body: formData,
        }})
        .then(function(resp) {{ return resp.json(); }})
        .then(function(data) {{
            spinner.style.display = 'none';
            resultDiv.style.display = 'block';

            if (data.success) {{
                resultDiv.innerHTML =
                    '<div style="color:var(--w-color-positive);font-weight:600;">' +
                    '✓ Ingestion complete</div>' +
                    '<div>Processed: ' + data.processed +
                    ' · Skipped: ' + data.skipped +
                    ' · Errors: ' + data.errors + '</div>';
                // Refresh after 2 seconds to update counts
                setTimeout(function() {{ location.reload(); }}, 2000);
            }} else {{
                resultDiv.innerHTML =
                    '<div style="color:var(--w-color-critical);">✗ Error: ' +
                    data.error + '</div>';
            }}
        }})
        .catch(function(err) {{
            spinner.style.display = 'none';
            resultDiv.style.display = 'block';
            resultDiv.innerHTML =
                '<div style="color:var(--w-color-critical);">✗ Network error: ' +
                err + '</div>';
        }})
        .finally(function() {{
            btn.disabled = false;
            btnRe.disabled = false;
        }});
    }}
    </script>
    """
    panels.append(mark_safe(panel_html))
