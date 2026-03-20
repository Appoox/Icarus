"""
Wagtail hooks for The Librarian.

Adds an "Ingest Archive" button to the Wagtail admin dashboard
and a sidebar menu item for archive documents.
"""
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.forms import Media

from wagtail import hooks
from wagtail.admin.menu import MenuItem
from wagtail.admin.ui.components import Component


# ── Sidebar menu item ────────────────────────────────────────────────────

@hooks.register("register_admin_menu_item")
def register_librarian_menu():
    return MenuItem(
        "Archive Search",
        reverse("the_librarian:search"),
        icon_name="search",
        order=900,
    )


# ── Dashboard "Ingest Archive" panel ─────────────────────────────────────

class LibrarianIngestPanel(Component):
    """A proper Wagtail Component for the ingestion dashboard panel."""
    order = 200

    def render_html(self, parent_context=None):
        from the_librarian.models import ArchiveDocument, DocumentChunk

        doc_count = ArchiveDocument.objects.count()
        chunk_count = DocumentChunk.objects.count()
        ingest_url = reverse("the_librarian:trigger_ingestion")

        return mark_safe(f"""
        <section class="panel" id="librarian-ingest-panel">

            <header class="panel__header">
                <div class="panel__header__title">
                <h2 class="panel__heading" style="display:flex;align-items:center;gap:8px;">
                    <svg class="icon icon-doc-full" aria-hidden="true" style="width:1em;height:1em;">
                        <use href="#icon-doc-full"></use>
                    </svg>
                    Archive Processing
                </h2>
                </div>
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
                    <button type="button" id="btn-stop-ingest"
                            class="button button-small button--warning"
                            onclick="librarianStopIngest()"
                            style="display:none;align-items:center;gap:6px;">
                        <svg class="icon" aria-hidden="true" style="width:1em;height:1em;">
                            <use href="#icon-cross"></use>
                        </svg>
                        Stop Ingestion
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
        function getCookie(name) {{
            var cookieValue = null;
            if (document.cookie && document.cookie !== '') {{
                var cookies = document.cookie.split(';');
                for (var i = 0; i < cookies.length; i++) {{
                    var cookie = cookies[i].trim();
                    if (cookie.substring(0, name.length + 1) === (name + '=')) {{
                        cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                        break;
                    }}
                }}
            }}
            return cookieValue;
        }}

        var stopRequested = false;

        function librarianStopIngest() {{
            stopRequested = true;
            var btnStop = document.getElementById('btn-stop-ingest');
            btnStop.disabled = true;
            btnStop.innerText = 'Stopping...';

            fetch('{reverse("the_librarian:stop_ingestion")}', {{
                method: 'POST',
                headers: {{ 'X-CSRFToken': getCookie('csrftoken') }}
            }});
        }}

        async function librarianIngest(force) {{
            var statusDiv = document.getElementById('ingest-status');
            var spinner = document.getElementById('ingest-spinner');
            var resultDiv = document.getElementById('ingest-result');
            var btn = document.getElementById('btn-ingest-archive');
            var btnRe = document.getElementById('btn-reingest-archive');
            var btnStop = document.getElementById('btn-stop-ingest');

            statusDiv.style.display = 'block';
            spinner.style.display = 'block';
            resultDiv.style.display = 'none';
            btn.disabled = true;
            btnRe.disabled = true;
            btnStop.style.display = 'inline-flex';
            btnStop.disabled = false;
            btnStop.innerHTML = 'Stop Ingestion';
            stopRequested = false;

            try {{
                // 1. Get pending list
                spinner.innerText = '🔍 Checking for new PDFs...';
                const listResp = await fetch('{reverse("the_librarian:get_pending_pdfs")}?force=' + force);
                const listData = await listResp.json();
                const queue = listData.pending || [];

                if (queue.length === 0) {{
                    spinner.style.display = 'none';
                    resultDiv.style.display = 'block';
                    resultDiv.innerHTML = '<div>No new PDFs to ingest.</div>';
                    btn.disabled = false;
                    btnRe.disabled = false;
                    btnStop.style.display = 'none';
                    return;
                }}

                let processedCount = 0;
                let errorCount = 0;
                let skippedCount = 0;

                // 2. Process one by one
                for (let i = 0; i < queue.length; i++) {{
                    if (stopRequested) break;

                    const filename = queue[i];
                    spinner.innerText = '⏳ Processing ' + (i + 1) + ' of ' + queue.length + ': ' + filename + '...';

                    var formData = new FormData();
                    formData.append('force', force ? 'true' : 'false');
                    formData.append('filename', filename);

                    try {{
                        const ingestResp = await fetch('{ingest_url}', {{
                            method: 'POST',
                            headers: {{ 'X-CSRFToken': getCookie('csrftoken') }},
                            body: formData,
                        }});
                        const data = await ingestResp.json();

                        if (data.success) {{
                            processedCount += data.processed;
                            skippedCount += data.skipped;
                            errorCount += data.errors;
                        }} else {{
                            errorCount++;
                        }}
                    }} catch (e) {{
                        errorCount++;
                    }}
                }}

                // 3. Final result
                spinner.style.display = 'none';
                resultDiv.style.display = 'block';
                btnStop.style.display = 'none';

                let msg = stopRequested ? '⚠ Ingestion stopped by user.' : '✓ Ingestion complete.';
                resultDiv.innerHTML =
                    '<div style="color:var(--w-color-positive);font-weight:600;">' + msg + '</div>' +
                    '<div>Processed: ' + processedCount +
                    ' · Skipped: ' + skippedCount +
                    ' · Errors: ' + errorCount + '</div>';

                if (!stopRequested) {{
                    setTimeout(function() {{ location.reload(); }}, 3000);
                }}
            }} catch (err) {{
                spinner.style.display = 'none';
                resultDiv.style.display = 'block';
                btnStop.style.display = 'none';
                resultDiv.innerHTML = '<div style="color:var(--w-color-critical);">✗ Error: ' + err.message + '</div>';
            }} finally {{
                btn.disabled = false;
                btnRe.disabled = false;
            }}
        }}
        </script>
        """)

    @property
    def media(self):
        return Media()


@hooks.register("construct_homepage_panels")
def add_ingest_panel(request, panels):
    """Add the ingestion control panel to the Wagtail admin dashboard."""
    panels.append(LibrarianIngestPanel())
