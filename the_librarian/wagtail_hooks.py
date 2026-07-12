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


class _SuperuserOnlyMenuItem(MenuItem):
    """A MenuItem that only appears for superusers — used here so the
    sidebar link doesn't lead non-superusers to a dead-end 403 page."""
    def is_shown(self, request):
        return request.user.is_superuser


@hooks.register("register_admin_menu_item")
def register_remote_worker_menu():
    return _SuperuserOnlyMenuItem(
        "Remote Worker",
        reverse("the_librarian:remote_worker_admin"),
        icon_name="cog",
        order=910,
    )


# ── Dashboard ingestion panel (local, in-process pipeline) ────────────────

class LibrarianIngestPanel(Component):
    """
    Wagtail dashboard panel for The Librarian's LOCAL in-process pipeline.
    Shows 4 stat cards and provides a manual "Re-ingest All" recovery
    action via Django-Q2. New issues are no longer ingested from here —
    saving an ArchiveIssue snippet queues its ingestion automatically.
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
            var btnRe      = document.getElementById('btn-reingest-archive');
            var btnStop    = document.getElementById('btn-stop-ingest');

            var stopRequested = false;

            function setButtons(busy) {{
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


# ── Wagtail Dashboard Hook Registration ──────────────────────────────────

@hooks.register("construct_homepage_panels")
def add_ingest_panel(request, panels):
    """Add the local ingestion control panel to the Wagtail admin dashboard (superusers only).
    The remote worker's controls used to live here too — moved to their own page,
    linked from the sidebar (see register_remote_worker_menu below), since that
    panel is reached rarely (generate a key, flip a switch) compared to the
    always-relevant ingestion stats this one shows on every visit."""
    if request.user.is_superuser:
        panels.append(LibrarianIngestPanel())


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