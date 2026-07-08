"""
Wagtail hooks for The Librarian.
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


# ── Dashboard ingestion panel (local, in-process pipeline) ────────────────

class LibrarianIngestPanel(Component):
    """
    Wagtail dashboard panel for The Librarian's LOCAL in-process ingestion.
    Shows 4 stat cards and provides one-click async ingestion of all
    pending ArchiveIssue PDFs via Django-Q2, running on this server.
    """
    order = 200

    def render_html(self, parent_context=None):
        from the_librarian.models import ArchiveDocument, DocumentChunk, ArchiveIssue

        doc_count    = ArchiveDocument.objects.count()
        chunk_count  = DocumentChunk.objects.count()
        article_count = (
            DocumentChunk.objects
            .filter(article__isnull=False)
            .values('article')
            .distinct()
            .count()
        )
        editorial_count = (
            DocumentChunk.objects
            .filter(issue__isnull=False)
            .values('issue')
            .distinct()
            .count()
        )

        ingest_url      = reverse("the_librarian:trigger_ingestion")
        stop_url        = reverse("the_librarian:stop_ingestion")
        pending_url     = reverse("the_librarian:get_pending_pdfs")
        task_status_base = reverse("the_librarian:task_status", args=["PLACEHOLDER"]).replace("PLACEHOLDER", "")

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

                <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:1em;margin-bottom:1.4em;">

                    <div style="background:var(--w-color-surface-field);border-radius:6px;padding:1em;text-align:center;">
                        <div style="font-size:1.8em;font-weight:700;" id="stat-docs">{doc_count}</div>
                        <div style="color:var(--w-color-text-meta);font-size:.8em;margin-top:.25em;">Archive PDFs ingested</div>
                    </div>

                    <div style="background:var(--w-color-surface-field);border-radius:6px;padding:1em;text-align:center;">
                        <div style="font-size:1.8em;font-weight:700;" id="stat-chunks">{chunk_count}</div>
                        <div style="color:var(--w-color-text-meta);font-size:.8em;margin-top:.25em;">Text chunks stored</div>
                    </div>

                    <div style="background:var(--w-color-surface-field);border-radius:6px;padding:1em;text-align:center;">
                        <div style="font-size:1.8em;font-weight:700;" id="stat-articles">{article_count}</div>
                        <div style="color:var(--w-color-text-meta);font-size:.8em;margin-top:.25em;">Articles indexed</div>
                    </div>

                    <div style="background:var(--w-color-surface-field);border-radius:6px;padding:1em;text-align:center;">
                        <div style="font-size:1.8em;font-weight:700;" id="stat-editorials">{editorial_count}</div>
                        <div style="color:var(--w-color-text-meta);font-size:.8em;margin-top:.25em;">Editorials indexed</div>
                    </div>

                </div>

                <div style="display:flex;justify-content:center;gap:.8em;flex-wrap:wrap;">
                    <button type="button" id="btn-ingest-archive"
                            class="button button-small button--primary"
                            onclick="librarianIngest(false)"
                            style="display:inline-flex;align-items:center;gap:6px;">
                        <svg class="icon" aria-hidden="true" style="width:1em;height:1em;">
                            <use href="#icon-download"></use>
                        </svg>
                        Ingest New Issues
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
                    <div id="ingest-spinner" style="display:none;color:var(--w-color-text-meta);"></div>
                    <div id="ingest-result"  style="display:none;"></div>
                </div>

            </div>
        </section>

        <script>
        (function () {{
            'use strict';

            function getCookie(name) {{
                var val = null;
                if (document.cookie) {{
                    document.cookie.split(';').forEach(function(c) {{
                        c = c.trim();
                        if (c.startsWith(name + '=')) {{
                            val = decodeURIComponent(c.slice(name.length + 1));
                        }}
                    }});
                }}
                return val;
            }}

            var statusDiv  = document.getElementById('ingest-status');
            var spinner    = document.getElementById('ingest-spinner');
            var resultDiv  = document.getElementById('ingest-result');
            var btnIngest  = document.getElementById('btn-ingest-archive');
            var btnRe      = document.getElementById('btn-reingest-archive');
            var btnStop    = document.getElementById('btn-stop-ingest');

            var stopRequested = false;

            function setButtons(busy) {{
                btnIngest.disabled = busy;
                btnRe.disabled     = busy;
                btnStop.style.display = busy ? 'inline-flex' : 'none';
                if (busy) {{ btnStop.disabled = false; btnStop.innerHTML = 'Stop Ingestion'; }}
                statusDiv.style.display = busy ? 'block' : 'none';
                spinner.style.display   = busy ? 'block' : 'none';
                resultDiv.style.display = 'none';
            }}

            function showResult(html) {{
                spinner.style.display  = 'none';
                resultDiv.style.display = 'block';
                resultDiv.innerHTML    = html;
            }}

            window.librarianStopIngest = function () {{
                stopRequested = true;
                btnStop.disabled    = true;
                btnStop.textContent = 'Stopping…';
                fetch('{stop_url}', {{
                    method: 'POST',
                    headers: {{ 'X-CSRFToken': getCookie('csrftoken') }}
                }});
            }};

            function pollTask(taskId, intervalMs, timeoutMs) {{
                return new Promise(function (resolve) {{
                    var elapsed = 0;
                    var timer = setInterval(function () {{
                        elapsed += intervalMs;
                        fetch('{task_status_base}' + taskId)
                            .then(function(r) {{ return r.json(); }})
                            .then(function(data) {{
                                if (data.status === 'success') {{
                                    clearInterval(timer);
                                    resolve('success');
                                }} else if (data.status === 'failed') {{
                                    clearInterval(timer);
                                    resolve('failed');
                                }} else if (elapsed >= timeoutMs) {{
                                    clearInterval(timer);
                                    resolve('timeout');
                                }}
                            }})
                            .catch(function() {{}});
                    }}, intervalMs);
                }});
            }}

            window.librarianIngest = async function (force) {{
                stopRequested = false;
                setButtons(true);
                spinner.textContent = '🔍 Checking for pending issues…';

                try {{
                    var listResp = await fetch('{pending_url}?force=' + force);
                    var listData = await listResp.json();
                    var queue    = listData.pending || [];

                    if (queue.length === 0) {{
                        setButtons(false);
                        statusDiv.style.display = 'block';
                        showResult('<div>✓ No new Archive Issues to ingest.</div>');
                        return;
                    }}

                    spinner.textContent = '⏳ Queuing ' + queue.length + ' issue(s)…';

                    var tasks = [];
                    for (var i = 0; i < queue.length; i++) {{
                        if (stopRequested) break;

                        var item = queue[i];
                        spinner.textContent = (
                            '⏳ Queuing ' + (i + 1) + '/' + queue.length + ': ' + item.title
                        );

                        try {{
                            var fd = new FormData();
                            fd.append('archive_issue_pk', item.pk);
                            fd.append('force', force ? 'true' : 'false');

                            var resp = await fetch('{ingest_url}', {{
                                method: 'POST',
                                headers: {{ 'X-CSRFToken': getCookie('csrftoken') }},
                                body: fd,
                            }});
                            var data = await resp.json();

                            if (data.task_id) {{
                                tasks.push({{ taskId: data.task_id, title: item.title }});
                            }} else if (data.sync_result) {{
                                tasks.push({{ taskId: null, title: item.title, done: true,
                                             success: data.sync_result.status !== 'error' }});
                            }}
                        }} catch (e) {{
                            tasks.push({{ taskId: null, title: item.title, done: true, success: false }});
                        }}
                    }}

                    if (tasks.length === 0) {{
                        setButtons(false);
                        statusDiv.style.display = 'block';
                        showResult('<div>⚠ Stopped before any tasks were queued.</div>');
                        return;
                    }}

                    var completed = 0, failed = 0;
                    var asyncTasks = tasks.filter(function(t) {{ return t.taskId && !t.done; }});
                    var syncDone   = tasks.filter(function(t) {{ return t.done; }});

                    syncDone.forEach(function(t) {{
                        if (t.success) completed++; else failed++;
                    }});

                    spinner.textContent = (
                        '⏳ Processing ' + asyncTasks.length + ' queued task(s)…'
                    );

                    var pollPromises = asyncTasks.map(function(t) {{
                        return pollTask(t.taskId, 3000, 600000).then(function(outcome) {{
                            if (outcome === 'success') completed++;
                            else failed++;
                            spinner.textContent = (
                                '⏳ ' + (completed + failed) + '/' + tasks.length + ' done…'
                            );
                        }});
                    }});

                    await Promise.all(pollPromises);

                    setButtons(false);
                    statusDiv.style.display = 'block';
                    var icon = stopRequested ? '⚠' : (failed === 0 ? '✓' : '⚠');
                    var msg  = stopRequested ? 'Stopped by user. ' : '';
                    showResult(
                        '<div style="font-weight:600;">' + icon + ' ' + msg +
                        'Completed: ' + completed + ' · Failed: ' + failed + '</div>'
                    );

                    if (!stopRequested) {{
                        setTimeout(function() {{ location.reload(); }}, 2500);
                    }}

                }} catch (err) {{
                    setButtons(false);
                    statusDiv.style.display = 'block';
                    showResult(
                        '<div style="color:var(--w-color-critical);">✗ Error: ' + err.message + '</div>'
                    );
                }}
            }};

        }})();
        </script>
        """)

    @property
    def media(self):
        return Media()


# ── Dashboard panel: remote ingestion worker (off-box OCR) ────────────────

class RemoteIngestionPanel(Component):
    """
    Dashboard panel for the API-key-gated remote-worker API. Distinct from
    LibrarianIngestPanel (which controls LOCAL in-process ingestion on this
    server) because this one controls whether an external, network-facing
    surface is reachable at all — plus lets an admin generate/revoke API
    keys for it.
    """
    order = 210

    def render_html(self, parent_context=None):
        toggle_url     = reverse("the_librarian:toggle_remote_ingestion")
        status_url     = reverse("the_librarian:remote_ingestion_status")
        list_keys_url  = reverse("the_librarian:list_api_keys")
        create_key_url = reverse("the_librarian:create_api_key")

        return mark_safe(f"""
        <section class="panel" id="remote-ingest-panel">
            <header class="panel__header">
                <h2 class="panel__heading">Remote Ingestion Worker (Off-Box OCR)</h2>
            </header>
            <div class="panel__content" style="padding:1.5em;">

                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1.2em;">
                    <div>
                        <strong id="ri-status-label" style="font-size:1.1em;">⚪ Disabled</strong>
                        <div id="ri-status-sub" style="color:var(--w-color-text-meta);font-size:0.85em;margin-top:2px;">
                            The worker API 403s every request while this is off, regardless of API key.
                            Stays in whatever state you leave it — no automatic timeout.
                        </div>
                    </div>
                    <button type="button" id="ri-toggle-btn" class="button button-primary">
                        Enable Remote Ingestion
                    </button>
                </div>

                <div style="font-size:0.8em;color:var(--w-color-text-meta);margin-bottom:0.4em;">Files downloaded by worker (since last enabled)</div>
                <div style="background:var(--w-color-surface-field);border-radius:6px;height:18px;overflow:hidden;margin-bottom:0.2em;">
                    <div id="ri-files-bar" style="background:#5b6ef5;height:100%;width:0%;transition:width .3s;"></div>
                </div>
                <div id="ri-files-label" style="font-size:0.85em;margin-bottom:1em;color:var(--w-color-text-meta);">0 / 0</div>

                <div style="font-size:0.8em;color:var(--w-color-text-meta);margin-bottom:0.4em;">
                    Vectors uploaded back (grows as batches arrive — no fixed total)
                </div>
                <div id="ri-vectors-count" style="font-size:1.6em;font-weight:800;margin-bottom:1.5em;">0</div>

                <hr style="border:none;border-top:1px solid var(--w-color-border-furniture);margin:0 0 1.2em;">

                <h3 style="font-size:1em;margin-bottom:0.8em;">API Keys</h3>
                <div style="display:flex;gap:8px;margin-bottom:1em;">
                    <input type="text" id="ri-newkey-name" placeholder="Label, e.g. Home workstation"
                           style="flex:1;padding:6px 10px;border:1px solid var(--w-color-border-furniture);border-radius:4px;">
                    <button type="button" id="ri-create-key-btn" class="button button-primary">+ Generate Key</button>
                </div>
                <div id="ri-newkey-reveal" style="display:none;background:#fff3e0;border:1px solid #ffb74d;border-radius:6px;padding:12px;margin-bottom:1em;">
                    <strong>⚠ Copy this key now — it will not be shown again:</strong>
                    <div style="display:flex;gap:8px;margin-top:8px;">
                        <code id="ri-newkey-value" style="flex:1;padding:6px 10px;background:#fff;border-radius:4px;overflow-x:auto;white-space:nowrap;"></code>
                        <button type="button" id="ri-newkey-copy-btn" class="button button-secondary button-small">Copy</button>
                    </div>
                </div>
                <table class="listing" style="width:100%;font-size:0.85em;margin-bottom:1.5em;">
                    <thead><tr><th>Name</th><th>Prefix</th><th>Created</th><th>Last Used</th><th>Status</th><th></th></tr></thead>
                    <tbody id="ri-keys-body"><tr><td colspan="6" style="text-align:center;">Loading…</td></tr></tbody>
                </table>

                <hr style="border:none;border-top:1px solid var(--w-color-border-furniture);margin:0 0 1.2em;">

                <h3 style="font-size:1em;margin-bottom:0.8em;">Recent Activity</h3>
                <table class="listing" style="width:100%;font-size:0.85em;">
                    <thead><tr><th>Time</th><th>Event</th><th>Key</th><th>IP</th><th>Detail</th></tr></thead>
                    <tbody id="ri-log-body"><tr><td colspan="5" style="text-align:center;">Loading…</td></tr></tbody>
                </table>
            </div>
        </section>

        <script>
        (function() {{
            var toggleBtn = document.getElementById('ri-toggle-btn');
            var statusLabel = document.getElementById('ri-status-label');
            var statusSub = document.getElementById('ri-status-sub');
            var filesBar = document.getElementById('ri-files-bar');
            var filesLabel = document.getElementById('ri-files-label');
            var vectorsCount = document.getElementById('ri-vectors-count');
            var logBody = document.getElementById('ri-log-body');

            var keysBody = document.getElementById('ri-keys-body');
            var createKeyBtn = document.getElementById('ri-create-key-btn');
            var newKeyNameInput = document.getElementById('ri-newkey-name');
            var newKeyReveal = document.getElementById('ri-newkey-reveal');
            var newKeyValue = document.getElementById('ri-newkey-value');
            var newKeyCopyBtn = document.getElementById('ri-newkey-copy-btn');

            function getCookie(name) {{
                var v = null;
                document.cookie.split(';').forEach(function(c) {{
                    c = c.trim();
                    if (c.startsWith(name + '=')) v = decodeURIComponent(c.slice(name.length + 1));
                }});
                return v;
            }}

            function refreshStatus() {{
                fetch('{status_url}').then(function(r) {{ return r.json(); }}).then(function(data) {{
                    statusLabel.textContent = data.enabled ? '🟢 Enabled' : '⚪ Disabled';
                    toggleBtn.textContent = data.enabled ? 'Disable Remote Ingestion' : 'Enable Remote Ingestion';
                    toggleBtn.className = 'button ' + (data.enabled ? 'button-secondary' : 'button-primary');

                    var total = data.pending_total_at_enable || 0;
                    var pct = total > 0 ? Math.min(100, (data.files_downloaded / total) * 100) : 0;
                    filesBar.style.width = pct + '%';
                    filesLabel.textContent = data.files_downloaded + ' / ' + total;
                    vectorsCount.textContent = data.vectors_uploaded.toLocaleString();

                    if (data.failed_auth_attempts > 0 || data.rejected_while_disabled > 0) {{
                        statusSub.innerHTML = 'Since enabling: <span style="color:var(--w-color-critical);">⚠ '
                            + data.failed_auth_attempts + ' failed auth, '
                            + data.rejected_while_disabled + ' rejected-while-disabled.</span>';
                    }}

                    logBody.innerHTML = data.recent_events.map(function(e) {{
                        return '<tr><td>' + e.created_at + '</td><td>' + e.event + '</td><td>'
                            + (e.api_key__name || '—') + '</td><td>' + (e.remote_addr || '—') + '</td><td>'
                            + (e.detail || '') + (e.chunk_count ? ' (' + e.chunk_count + ' chunks)' : '')
                            + '</td></tr>';
                    }}).join('') || '<tr><td colspan="5" style="text-align:center;">No activity yet.</td></tr>';
                }});
            }}

            function refreshKeys() {{
                fetch('{list_keys_url}').then(function(r) {{ return r.json(); }}).then(function(data) {{
                    keysBody.innerHTML = data.keys.map(function(k) {{
                        var statusHtml = k.is_active
                            ? '<span style="color:#2e7d32;font-weight:600;">Active</span>'
                            : '<span style="color:#c62828;">Revoked ' + (k.revoked_at || '') + '</span>';
                        var actionHtml = k.is_active
                            ? '<button type="button" class="button button-small button-secondary ri-revoke-btn" data-id="' + k.id + '">Revoke</button>'
                            : '';
                        return '<tr><td>' + k.name + '</td><td><code>' + k.key_prefix + '…</code></td><td>'
                            + k.created_at + '</td><td>' + k.last_used_at + '</td><td>' + statusHtml
                            + '</td><td>' + actionHtml + '</td></tr>';
                    }}).join('') || '<tr><td colspan="6" style="text-align:center;">No API keys yet.</td></tr>';

                    keysBody.querySelectorAll('.ri-revoke-btn').forEach(function(btn) {{
                        btn.addEventListener('click', function() {{
                            if (!confirm('Revoke this key? Any worker still using it will immediately lose access.')) return;
                            fetch('/librarian/api/worker/keys/' + btn.dataset.id + '/revoke/', {{
                                method: 'POST', headers: {{'X-CSRFToken': getCookie('csrftoken')}}
                            }}).then(refreshKeys);
                        }});
                    }});
                }});
            }}

            toggleBtn.addEventListener('click', function() {{
                toggleBtn.disabled = true;
                fetch('{toggle_url}', {{ method: 'POST', headers: {{'X-CSRFToken': getCookie('csrftoken')}} }})
                    .then(function() {{ toggleBtn.disabled = false; refreshStatus(); }});
            }});

            createKeyBtn.addEventListener('click', function() {{
                var name = newKeyNameInput.value.trim();
                if (!name) {{ alert('Enter a label for this key first.'); return; }}
                fetch('{create_key_url}', {{
                    method: 'POST',
                    headers: {{'X-CSRFToken': getCookie('csrftoken'), 'Content-Type': 'application/json'}},
                    body: JSON.stringify({{name: name}})
                }}).then(function(r) {{ return r.json(); }}).then(function(data) {{
                    if (data.error) {{ alert(data.error); return; }}
                    newKeyValue.textContent = data.raw_key;
                    newKeyReveal.style.display = 'block';
                    newKeyNameInput.value = '';
                    refreshKeys();
                }});
            }});

            newKeyCopyBtn.addEventListener('click', function() {{
                navigator.clipboard.writeText(newKeyValue.textContent);
                newKeyCopyBtn.textContent = 'Copied!';
                setTimeout(function() {{ newKeyCopyBtn.textContent = 'Copy'; }}, 1500);
            }});

            refreshStatus();
            refreshKeys();
            setInterval(refreshStatus, 5000);
        }})();
        </script>
        """)

    @property
    def media(self):
        return Media()


# ── Wagtail Dashboard Hook Registration ──────────────────────────────────

@hooks.register("construct_homepage_panels")
def add_ingest_panel(request, panels):
    """Add the ingestion control panels to the Wagtail admin dashboard (superusers only)."""
    if request.user.is_superuser:
        panels.append(LibrarianIngestPanel())
        panels.append(RemoteIngestionPanel())


# ── Auto-indexing hooks ──────────────────────────────────────────────────

@hooks.register("after_publish_page")
def auto_index_content(request, page):
    """
    Automatically (re)index Articles, Literati authors, and Issue editorials
    when a page is published. Uses Django-Q2 async tasks when available;
    falls back to synchronous transaction.on_commit() execution.
    """
    from django.db import transaction

    try:
        from django_q.tasks import async_task
        use_async = True
    except ImportError:
        use_async = False

    from articles.models import Article
    from literati.models import Literati
    from issue.models import Issue

    specific = page.specific

    if isinstance(specific, Article):
        _pk = specific.pk
        if use_async:
            transaction.on_commit(lambda: async_task(
                'the_librarian.tasks.async_ingest_article', _pk
            ))
        else:
            from the_librarian.services.indexing import index_article
            transaction.on_commit(lambda: index_article(_pk))

    elif isinstance(specific, Literati):
        _pk = specific.pk
        if use_async:
            transaction.on_commit(lambda: async_task(
                'the_librarian.tasks.async_ingest_author', _pk
            ))
        else:
            from the_librarian.services.indexing import index_author
            transaction.on_commit(lambda: index_author(_pk))

    elif isinstance(specific, Issue):
        _pk = specific.pk
        if use_async:
            transaction.on_commit(lambda: async_task(
                'the_librarian.tasks.async_index_editorial', _pk
            ))
        else:
            from the_librarian.services.indexing import index_editorial
            transaction.on_commit(lambda: index_editorial(_pk))


@hooks.register("after_create_snippet")
@hooks.register("after_edit_snippet")
def index_topic_snippet(request, instance):
    """
    Automatically index Topic snippets when created or edited.
    """
    from django.db import transaction
    from issue.models import Topic

    if not isinstance(instance, Topic):
        return

    try:
        from django_q.tasks import async_task
        use_async = True
    except ImportError:
        use_async = False

    _pk = instance.pk

    if use_async:
        transaction.on_commit(lambda: async_task(
            'the_librarian.tasks.async_index_topic', _pk
        ))
    else:
        from the_librarian.services.indexing import index_topic
        transaction.on_commit(lambda: index_topic(_pk))

# ── ArchiveIssue Wagtail snippet viewset ─────────────────────────────────

from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet
from the_librarian.models import ArchiveIssue


class ArchiveIssueViewSet(SnippetViewSet):
    model       = ArchiveIssue
    menu_label  = "Archive Issues"
    icon        = "doc-full"
    menu_order  = 300

    list_display  = ["title", "year", "get_month_display", "volume", "issue_number"]
    list_filter   = ["year", "volume"]
    search_fields = ["title", "description"]
    ordering      = ["-year", "-month", "-issue_number"]

    def user_can_create(self, user):
        return user.is_active and user.is_staff

    def user_can_edit_obj(self, user, obj):
        return user.is_active and user.is_staff

    def user_can_delete_obj(self, user, obj):
        return user.is_active and user.is_staff


register_snippet(ArchiveIssueViewSet)


# ── Auto-link ArchiveIssue ↔ ArchiveDocument after snippet save ──────────

@hooks.register("after_create_snippet")
@hooks.register("after_edit_snippet")
def auto_link_archive_document(request, instance):
    """
    After saving a ArchiveIssue, try to find a matching ArchiveDocument by
    the uploaded PDF's basename and link them.
    """
    import os
    from the_librarian.models import ArchiveDocument

    if not isinstance(instance, ArchiveIssue):
        return

    if instance.archive_document_id:
        return

    if not instance.pdf_file:
        return

    basename = os.path.basename(instance.pdf_file.name)
    try:
        doc = ArchiveDocument.objects.get(filename=basename)
        ArchiveIssue.objects.filter(pk=instance.pk).update(archive_document=doc)
    except ArchiveDocument.DoesNotExist:
        pass  # PDF not yet ingested; will be linked when Q2 task completes