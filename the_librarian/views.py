from functools import wraps
from pathlib import Path
import os
import json

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, Http404, FileResponse, HttpResponseForbidden
from django.conf import settings
from django.urls import reverse
from django.apps import apps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

import logging

from the_librarian.models import (
    ArchiveDocument, DocumentChunk,
    ArchiveIssue, MONTH_CHOICES,
    LibrarianAPIKey, LibrarianRemoteIngestSettings, LibrarianWorkerAccessLog,
)
from the_librarian.services.search import (
    search_similar,
    search_keyword,
    search_hybrid,
    search_by_document,
)
from the_librarian.services.ingestion import persist_ingested_batch
from the_librarian.services.vector_schema import validate_batch, VectorValidationError
from the_librarian import page_cache

logger = logging.getLogger(__name__)


# ── Helper function for search title replacement ─────────────────────────

def _resolve_pdf_display_titles(results):
    """
    Collects document IDs from results, queries titles efficiently,
    and maps issue save titles onto PDF result blocks.
    Also dynamically resolves authentic URLs for Topics.
    """
    # 1. Resolve PDFs
    pdf_doc_ids = [r["document_id"] for r in results if r.get("type") == "pdf" and "document_id" in r]
    if pdf_doc_ids:
        docs = ArchiveDocument.objects.filter(id__in=pdf_doc_ids).select_related('Archive_Issue')
        title_map = {d.id: d.display_title for d in docs}
        for r in results:
            if r.get("type") == "pdf" and r.get("document_id") in title_map:
                r["title"] = title_map[r["document_id"]]

    # 2. Resolve Topic URLs cleanly (prevents empty slugs for Malayalam titles)
    topic_ids = [r.get("topic_id") or r.get("document_id") for r in results if r.get("type") == "topic"]
    topic_ids = [tid for tid in topic_ids if tid]

    if topic_ids:
        try:
            Topic = apps.get_model('issue', 'Topic')
            topics = Topic.objects.filter(id__in=topic_ids)
            topic_map = {t.id: t for t in topics}
            for r in results:
                if r.get("type") == "topic":
                    tid = r.get("topic_id") or r.get("document_id")
                    if tid in topic_map:
                        r["title"] = topic_map[tid].name
                        r["url"] = reverse('topic_detail', args=[topic_map[tid].slug])
        except Exception as e:
            logger.warning(f"Could not resolve topic URLs: {e}")

    return results


# ── Pre-rendered page images (server-side cache; see page_cache.py) ──────

def _serve_page_meta(pdf_path):
    """JSON {"page_count": N} for a PDF, via the on-disk page cache."""
    return JsonResponse({"page_count": page_cache.get_page_count(pdf_path)})


def _serve_page_image(pdf_path, page_number):
    """A single page's pre-rendered (and disk-cached) image."""
    try:
        image_path = page_cache.get_page_image_path(pdf_path, page_number)
    except Exception:
        logger.exception("Failed to render page %s of %s", page_number, pdf_path)
        raise Http404("Could not render that page")

    response = FileResponse(open(image_path, "rb"), content_type="image/webp")
    response["Cache-Control"] = "public, max-age=86400, immutable"
    return response


def _page_image_url_template(url_name, *args):
    """
    Build a page-image URL containing a `{page}` placeholder the client
    can substitute per page, from a URL pattern whose last positional
    argument is page_number.
    """
    placeholder = 999999999
    url = reverse(f"the_librarian:{url_name}", args=[*args, placeholder])
    return url.replace(str(placeholder), "{page}")


# ── Access-control decorators ──────────────────────────────────────────────

def superuser_required(view_func):
    """Restricts access to superusers only; returns JSON 403 for API views."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_superuser:
            return JsonResponse(
                {"error": "Only administrators can perform this action."},
                status=403,
            )
        return view_func(request, *args, **kwargs)
    return _wrapped


def _log_worker_event(request, event, *, archive_issue=None, detail="", chunk_count=None):
    LibrarianWorkerAccessLog.objects.create(
        remote_addr=request.META.get("REMOTE_ADDR"),
        event=event,
        detail=detail,
        archive_issue=archive_issue,
        chunk_count=chunk_count,
        api_key=getattr(request, "librarian_api_key", None),
    )


def ingest_worker_token_required(view_func):
    """
    Authenticates the remote OCR worker via one of possibly several active
    LibrarianAPIKey records (managed from the Remote Ingestion Worker
    dashboard panel), AND requires LibrarianRemoteIngestSettings.enabled to
    be True. Neither condition alone is sufficient — a valid key while the
    switch is off, or the switch on with no valid key, both 403.

    On success, stashes the matching LibrarianAPIKey on the request so
    _log_worker_event can record which key was used.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        ri_settings = LibrarianRemoteIngestSettings.load()

        if not ri_settings.is_currently_enabled:
            _log_worker_event(request, "disabled", detail=request.path)
            return JsonResponse({"error": "Remote ingestion is currently disabled."}, status=403)

        provided = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        api_key = LibrarianAPIKey.authenticate(provided)

        if api_key is None:
            attempted_prefix = f"{provided[:10]}…" if provided else "(none)"
            _log_worker_event(
                request, "auth_failed",
                detail=f"{request.path} — attempted prefix: {attempted_prefix}",
            )
            return JsonResponse({"error": "Invalid or missing worker token."}, status=403)

        LibrarianAPIKey.objects.filter(pk=api_key.pk).update(last_used_at=timezone.now())
        request.librarian_api_key = api_key

        return view_func(request, *args, **kwargs)
    return _wrapped


# ── Search views ──────────────────────────────────────────────────────────

def search_view(request):
    """Render the search page with optional results."""
    query = request.GET.get("q", "").strip()
    top_k = int(request.GET.get("top_k", 5))
    mode = request.GET.get("mode", "hybrid").lower()
    results = []

    if query:
        if mode == "keyword":
            results = search_keyword(query, top_k=top_k)
        elif mode == "similarity":
            results = search_similar(query, top_k=top_k)
        else:
            results = search_hybrid(query, top_k=top_k)

        results = _resolve_pdf_display_titles(results)

    return render(request, "the_librarian/search.html", {
        "query": query,
        "results": results,
        "top_k": top_k,
        "mode": mode,
    })


def search_api(request):
    """JSON API for hybrid / similarity / keyword search (used by the header AJAX)."""
    query = request.GET.get("q", "").strip()
    top_k = int(request.GET.get("top_k", 5))
    document = request.GET.get("document", "").strip()

    if not query:
        return JsonResponse({"error": "Missing 'q' parameter"}, status=400)

    if document:
        results = search_by_document(document, query, top_k=top_k)
    else:
        mode = request.GET.get("mode", "hybrid").lower()
        if mode == "keyword":
            results = search_keyword(query, top_k=top_k)
        elif mode == "similarity":
            results = search_similar(query, top_k=top_k)
        else:
            results = search_hybrid(query, top_k=top_k)

    results = _resolve_pdf_display_titles(results)

    return JsonResponse({"query": query, "results": results})


# ── ArchiveDocument ViewerJS display ─────────────────────────────────────

def _resolve_document_pdf_path(document_id):
    """
    Resolve an ArchiveDocument's PDF to an absolute filesystem path.

    Resolution order:
    1. ARCHIVE_DIR / doc.file_path   — filesystem-ingested PDFs
    2. MEDIA_ROOT  / doc.file_path   — ArchiveIssue-uploaded PDFs
    """
    try:
        doc = ArchiveDocument.objects.get(pk=document_id)
    except ArchiveDocument.DoesNotExist:
        raise Http404("Document not found")

    pdf_path = Path(settings.ARCHIVE_DIR) / doc.file_path
    if not pdf_path.exists():
        pdf_path = Path(settings.MEDIA_ROOT) / doc.file_path

    if not pdf_path.exists():
        raise Http404("PDF file not found on disk")

    return pdf_path


@login_required
def viewer_view(request, document_id):
    """Display an ingested ArchiveDocument PDF via ViewerJS."""
    try:
        doc = ArchiveDocument.objects.get(pk=document_id)
    except ArchiveDocument.DoesNotExist:
        raise Http404("Document not found")

    page = int(request.GET.get("page", 1))

    pdf_url = reverse("the_librarian:serve_pdf", args=[document_id])

    return render(request, "the_librarian/viewer.html", {
        "document": doc,
        "page": page,
        "pdf_url": pdf_url,
        "page_meta_url": reverse("the_librarian:document_page_meta", args=[document_id]),
        "page_image_url_template": _page_image_url_template("document_page_image", document_id),
    })


@login_required
def serve_pdf(request, document_id):
    """
    Serve an ingested ArchiveDocument PDF.
    """
    try:
        doc = ArchiveDocument.objects.get(pk=document_id)
    except ArchiveDocument.DoesNotExist:
        raise Http404("Document not found")

    pdf_path = _resolve_document_pdf_path(document_id)

    return FileResponse(
        open(pdf_path, "rb"),
        content_type="application/pdf",
        filename=doc.filename,
    )


@login_required
@require_GET
def document_page_meta(request, document_id):
    """Page count for an ArchiveDocument's PDF, as pre-rendered page images."""
    return _serve_page_meta(_resolve_document_pdf_path(document_id))


@login_required
@require_GET
def document_page_image(request, document_id, page_number):
    """A single pre-rendered (and disk-cached) page image for an ArchiveDocument."""
    return _serve_page_image(_resolve_document_pdf_path(document_id), page_number)


# ── ArchiveIssue viewer ──────────────────────────────────────────────────

@require_GET
def archive_list(request):
    """Public archive grid: filterable, sortable Archive Issue cards."""
    def _int_or_none(key):
        try:
            return int(request.GET[key])
        except (KeyError, ValueError, TypeError):
            return None

    selected_year   = _int_or_none('year')
    selected_month  = _int_or_none('month')
    selected_volume = _int_or_none('volume')
    sort            = request.GET.get('sort', 'newest')

    issues = ArchiveIssue.objects.all()

    if selected_year is not None:
        issues = issues.filter(year=selected_year)
    if selected_month is not None:
        issues = issues.filter(month=selected_month)
    if selected_volume is not None:
        issues = issues.filter(volume=selected_volume)

    if sort == 'oldest':
        issues = issues.order_by('year', 'month', 'issue_number')
    elif sort == 'volume':
        issues = issues.order_by('volume', 'issue_number')
    else:
        issues = issues.order_by('-year', '-month', '-issue_number')

    all_issues  = ArchiveIssue.objects.all()
    all_years   = all_issues.values_list('year', flat=True).distinct().order_by('-year')
    all_volumes = all_issues.values_list('volume', flat=True).distinct().order_by('volume')

    has_filter = any([selected_year, selected_month, selected_volume])

    return render(request, "the_librarian/archive_list.html", {
        "issues":          issues,
        "all_years":       all_years,
        "all_volumes":     all_volumes,
        "all_months":      MONTH_CHOICES,
        "selected_year":   selected_year,
        "selected_month":  selected_month,
        "selected_volume": selected_volume,
        "sort":            sort,
        "has_filter":      has_filter,
        "issue_count":     issues.count(),
    })


def _resolve_issue_pdf_path(issue_id):
    """
    Resolve an ArchiveIssue's PDF to an absolute filesystem path, for the
    page-image views below.
    """
    issue = get_object_or_404(ArchiveIssue, pk=issue_id)
    if not issue.pdf_file:
        raise Http404("No PDF file attached to this issue.")
    try:
        return Path(issue.pdf_file.path)
    except NotImplementedError:
        raise Http404("Page rendering isn't supported for this issue's storage backend.")


@login_required
@require_GET
def magazine_viewer(request, issue_id):
    """Display a ArchiveIssue PDF via ViewerJS."""
    issue = get_object_or_404(ArchiveIssue, pk=issue_id)

    pdf_url = reverse("the_librarian:serve_magazine_pdf", args=[issue_id])

    return render(request, "the_librarian/viewer.html", {
        "document": {
            "filename":    issue.title,
            "total_pages": "?",
        },
        "page":    int(request.GET.get("page", 1)),
        "pdf_url": pdf_url,
        "page_meta_url": reverse("the_librarian:magazine_page_meta", args=[issue_id]),
        "page_image_url_template": _page_image_url_template("magazine_page_image", issue_id),
    })


@login_required
@require_GET
def magazine_page_meta(request, issue_id):
    """Page count for an ArchiveIssue's PDF, as pre-rendered page images."""
    return _serve_page_meta(_resolve_issue_pdf_path(issue_id))


@login_required
@require_GET
def magazine_page_image(request, issue_id, page_number):
    """A single pre-rendered (and disk-cached) page image for an ArchiveIssue."""
    return _serve_page_image(_resolve_issue_pdf_path(issue_id), page_number)


@login_required
@require_GET
def serve_magazine_pdf(request, issue_id):
    """Inline-serve the PDF for a ArchiveIssue (used by the ViewerJS iframe)."""
    issue = get_object_or_404(ArchiveIssue, pk=issue_id)

    if not issue.pdf_file:
        raise Http404("No PDF file attached to this issue.")

    try:
        return FileResponse(
            issue.pdf_file.open("rb"),
            content_type="application/pdf",
            filename=issue.pdf_file.name.rsplit("/", 1)[-1],
        )
    except FileNotFoundError:
        raise Http404("PDF file not found on disk.")


@login_required
@require_GET
def download_magazine_pdf(request, issue_id):
    """Force-download the PDF for a ArchiveIssue."""
    issue = get_object_or_404(ArchiveIssue, pk=issue_id)

    if not issue.pdf_file:
        raise Http404("No PDF file attached to this issue.")

    try:
        return FileResponse(
            issue.pdf_file.open("rb"),
            content_type="application/pdf",
            as_attachment=True,
            filename=issue.pdf_file.name.rsplit("/", 1)[-1],
        )
    except FileNotFoundError:
        raise Http404("PDF file not found on disk.")


# ── Direct archive filesystem access ─────────────────────────────────────

def _resolve_archive_filename_path(filename):
    """
    Resolve a raw filesystem PDF's absolute path from its filename,
    validating it the same way archive_download does.
    """
    safe_filename = os.path.basename(filename)
    pdf_path = Path(settings.ARCHIVE_DIR) / safe_filename

    if not pdf_path.exists() or not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
        raise Http404("PDF file not found on disk")

    return pdf_path


@login_required
@require_GET
def archive_viewer(request, filename):
    """View a raw filesystem PDF from ARCHIVE_DIR via ViewerJS."""
    pdf_url = reverse("the_librarian:archive_download", args=[filename])

    return render(request, "the_librarian/viewer.html", {
        "document": {"filename": filename, "total_pages": "?"},
        "page": 1,
        "pdf_url": pdf_url,
        "page_meta_url": reverse("the_librarian:archive_page_meta", args=[filename]),
        "page_image_url_template": _page_image_url_template("archive_page_image", filename),
    })


@login_required
@require_GET
def archive_download(request, filename):
    """Serve a raw PDF from ARCHIVE_DIR by filename."""
    safe_filename = os.path.basename(filename)
    pdf_path = _resolve_archive_filename_path(filename)

    return FileResponse(
        open(pdf_path, "rb"),
        content_type="application/pdf",
        filename=safe_filename,
    )


@login_required
@require_GET
def archive_page_meta(request, filename):
    """Page count for a raw filesystem PDF, as pre-rendered page images."""
    return _serve_page_meta(_resolve_archive_filename_path(filename))


@login_required
@require_GET
def archive_page_image(request, filename, page_number):
    """A single pre-rendered (and disk-cached) page image for a raw filesystem PDF."""
    return _serve_page_image(_resolve_archive_filename_path(filename), page_number)


# ── Admin ingestion triggers (local, in-process pipeline) ────────────────

@superuser_required
@require_POST
def trigger_ingestion(request):
    """
    Admin-only: queue an async Django-Q2 ingestion task for one ArchiveIssue.
    """
    from the_librarian.services.ingestion import clear_stop_signal

    archive_issue_pk = request.POST.get("archive_issue_pk", "").strip()
    force = request.POST.get("force", "false").lower() == "true"

    clear_stop_signal()

    if not archive_issue_pk:
        return JsonResponse(
            {"error": "Missing archive_issue_pk parameter"}, status=400
        )

    try:
        pk = int(archive_issue_pk)
    except ValueError:
        return JsonResponse({"error": "archive_issue_pk must be an integer"}, status=400)

    try:
        from django_q.tasks import async_task
        task_id = async_task(
            'the_librarian.tasks.async_ingest_archive_issue',
            pk,
            force,
        )
        logger.info(
            "Queued ingestion task %s for ArchiveIssue pk=%s (force=%s)",
            task_id, pk, force,
        )
        return JsonResponse({"success": True, "task_id": task_id})
    except ImportError:
        logger.warning(
            "django_q not available; running ingestion synchronously for pk=%s", pk
        )
        from the_librarian.services.ingestion import ingest_archive_issue
        try:
            result = ingest_archive_issue(pk, force=force)
            return JsonResponse({
                "success": True,
                "task_id": None,
                "sync_result": result,
            })
        except Exception as e:
            logger.exception("Synchronous ingestion failed for ArchiveIssue pk=%s", pk)
            return JsonResponse({"success": False, "error": str(e)}, status=500)
    except Exception as e:
        logger.exception("Failed to queue ingestion task for ArchiveIssue pk=%s", pk)
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@superuser_required
@require_POST
def stop_ingestion(request):
    """Admin-only: signal a running ingestion to stop after the current page."""
    from the_librarian.services.ingestion import request_stop
    request_stop()
    return JsonResponse({"success": True, "message": "Stop requested"})


@superuser_required
@require_GET
def get_pending_pdfs_view(request):
    """
    Admin-only: return ArchiveIssue objects not yet ingested (local dashboard panel).
    """
    force = request.GET.get("force", "false").lower() == "true"

    if force:
        issues_qs = ArchiveIssue.objects.all()
    else:
        issues_qs = ArchiveIssue.objects.filter(archive_document__isnull=True)

    pending = [
        {"pk": i.pk, "title": str(i)}
        for i in issues_qs.order_by('-year', '-month')
    ]
    return JsonResponse({"success": True, "pending": pending})


@require_GET
def task_status_view(request, task_id):
    """
    Check the completion status of a Django-Q2 async task by its ID.
    """
    try:
        from django_q.models import Task
        try:
            task = Task.objects.get(id=task_id)
            return JsonResponse({
                "status": "success" if task.success else "failed",
                "result": str(task.result)[:300] if task.result else "",
            })
        except Task.DoesNotExist:
            return JsonResponse({"status": "pending"})
    except ImportError:
        return JsonResponse({
            "status": "unavailable",
            "message": "django_q is not installed",
        })


# ─────────────────────────────────────────────────────────────────────────
# Remote ingestion worker (off-box OCR) — API-key + kill-switch gated
# ─────────────────────────────────────────────────────────────────────────

@ingest_worker_token_required
@require_GET
def list_pending_for_worker(request):
    """
    Token-authenticated equivalent of get_pending_pdfs_view, for the remote
    OCR worker. Includes a direct download URL and the (year, month) pair
    per pending PDF, so the worker never needs to invent a filename itself.
    """
    force = request.GET.get("force", "false").lower() == "true"
    issues_qs = (
        ArchiveIssue.objects.all() if force
        else ArchiveIssue.objects.filter(archive_document__isnull=True)
    )
    pending = [
        {
            "pk": i.pk,
            "title": str(i),
            "year": i.year,
            "month": i.month,
            "download_url": request.build_absolute_uri(
                reverse("the_librarian:worker_download_pdf", args=[i.pk])
            ),
        }
        for i in issues_qs.order_by('-year', '-month')
    ]
    _log_worker_event(request, "pending_check", detail=f"{len(pending)} pending, force={force}")
    return JsonResponse({"success": True, "pending": pending})


@ingest_worker_token_required
@require_GET
def worker_download_pdf(request, issue_id):
    """
    Token-authenticated PDF download for the remote worker only. Kept
    separate from download_magazine_pdf (which is session-authenticated for
    human readers) — a headless script authenticates via API key, never a
    Django session.
    """
    issue = get_object_or_404(ArchiveIssue, pk=issue_id)
    if not issue.pdf_file:
        return JsonResponse({"error": "No PDF attached to this issue."}, status=404)

    _log_worker_event(request, "download", archive_issue=issue, detail=issue.pdf_file.name)

    try:
        return FileResponse(
            issue.pdf_file.open("rb"),
            content_type="application/pdf",
            filename=issue.pdf_file.name.rsplit("/", 1)[-1],
        )
    except FileNotFoundError:
        raise Http404("PDF file not found on disk.")


@csrf_exempt  # bearer-token API, no browser session — CSRF protection doesn't apply
@ingest_worker_token_required
@require_POST
def submit_ingested_chunks(request):
    """
    Receives ONE BATCH of pre-OCR'd, pre-embedded chunks for a single
    ArchiveIssue. Batched because a document's chunk count is unpredictable
    ahead of time — capping each request at MAX_CHUNKS_PER_BATCH keeps every
    request body small regardless of how large any one document turns out
    to be, rather than raising limits and hoping nothing ever exceeds them.

    The document is identified purely by archive_issue_pk — the worker
    never gets to name it, closing the "same issue under a different
    filename" loophole at the wire-contract level, not just the DB level.
    """
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    archive_issue_pk = payload.get("archive_issue_pk")
    issue = ArchiveIssue.objects.filter(pk=archive_issue_pk).first()
    if not issue:
        _log_worker_event(request, "submit_rejected", detail="unknown archive_issue_pk")
        return JsonResponse({"error": "Unknown or missing archive_issue_pk."}, status=400)

    batch_index = payload.get("batch_index")
    is_final = bool(payload.get("is_final", False))
    total_pages = payload.get("total_pages")

    if not isinstance(batch_index, int) or isinstance(batch_index, bool) or batch_index < 0:
        return JsonResponse({"error": "batch_index must be a non-negative integer."}, status=400)
    if not isinstance(total_pages, int) or isinstance(total_pages, bool) or not (0 < total_pages <= 2000):
        return JsonResponse({"error": "total_pages must be a plausible positive integer."}, status=400)

    try:
        records = validate_batch(payload.get("chunks"), expected_dim=settings.LIBRARIAN_EMBEDDING_DIM)
    except VectorValidationError as e:
        _log_worker_event(request, "submit_rejected", archive_issue=issue, detail=str(e))
        return JsonResponse({"error": str(e)}, status=400)

    # Commonsense guard: refuse to let an empty first batch wipe a working index.
    if batch_index == 0 and not records:
        return JsonResponse(
            {"error": "Refusing to clear existing chunks with an empty first batch."}, status=400
        )

    archive_doc = persist_ingested_batch(
        archive_issue=issue, batch_index=batch_index, is_final=is_final,
        total_pages=total_pages, records=records,
    )

    _log_worker_event(
        request, "submit_ok", archive_issue=issue,
        detail=f"batch {batch_index}{' (final)' if is_final else ''}",
        chunk_count=len(records),
    )

    return JsonResponse({
        "success": True, "archive_issue_pk": issue.pk, "document_id": archive_doc.pk,
        "chunks_in_batch": len(records), "is_final": is_final,
    })


@login_required
@require_POST
def toggle_remote_ingestion(request):
    """
    Browser-facing, superuser-only toggle for the remote-worker kill-switch.
    Ordinary session auth + CSRF apply here — this is a dashboard button
    click, not the worker's own traffic.
    """
    if not request.user.is_superuser:
        return HttpResponseForbidden()

    ri_settings = LibrarianRemoteIngestSettings.load()
    turning_on = not ri_settings.enabled

    if turning_on:
        ri_settings.enabled = True
        ri_settings.enabled_at = timezone.now()
        ri_settings.enabled_by = request.user
        ri_settings.pending_total_at_enable = ArchiveIssue.objects.filter(
            archive_document__isnull=True
        ).count()
    else:
        ri_settings.enabled = False

    ri_settings.save()
    return JsonResponse({"success": True, "enabled": ri_settings.is_currently_enabled})


@login_required
@require_GET
def remote_ingestion_status(request):
    """
    Powers the dashboard panel's progress bars and recent-activity table.
    """
    if not request.user.is_superuser:
        return HttpResponseForbidden()

    ri_settings = LibrarianRemoteIngestSettings.load()
    since = ri_settings.enabled_at or timezone.now()

    logs_since = LibrarianWorkerAccessLog.objects.filter(created_at__gte=since)
    files_downloaded = logs_since.filter(event="download").values("archive_issue_id").distinct().count()
    vectors_uploaded = logs_since.filter(event="submit_ok").aggregate(total=Sum("chunk_count"))["total"] or 0

    recent = list(
        LibrarianWorkerAccessLog.objects.select_related('api_key').all()[:15]
        .values("created_at", "remote_addr", "event", "detail", "chunk_count", "api_key__name")
    )
    for r in recent:
        r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")

    return JsonResponse({
        "enabled": ri_settings.is_currently_enabled,
        "pending_total_at_enable": ri_settings.pending_total_at_enable,
        "files_downloaded": files_downloaded,
        "vectors_uploaded": vectors_uploaded,
        "failed_auth_attempts": logs_since.filter(event="auth_failed").count(),
        "rejected_while_disabled": logs_since.filter(event="disabled").count(),
        "recent_events": recent,
    })


# ── API key management (browser-facing, superuser-only) ──────────────────

@login_required
@require_GET
def list_api_keys(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden()

    keys = LibrarianAPIKey.objects.all().order_by('-created_at')
    data = [{
        "id": k.pk,
        "name": k.name,
        "key_prefix": k.key_prefix,
        "created_at": k.created_at.strftime("%Y-%m-%d %H:%M"),
        "created_by": str(k.created_by) if k.created_by else "—",
        "last_used_at": k.last_used_at.strftime("%Y-%m-%d %H:%M") if k.last_used_at else "Never",
        "is_active": k.is_active,
        "revoked_at": k.revoked_at.strftime("%Y-%m-%d %H:%M") if k.revoked_at else None,
    } for k in keys]
    return JsonResponse({"keys": data})


@login_required
@require_POST
def create_api_key(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden()

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        payload = {}

    name = (payload.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "A name/label is required for the new key."}, status=400)
    if len(name) > 100:
        return JsonResponse({"error": "Name is too long (max 100 characters)."}, status=400)

    instance, raw_key = LibrarianAPIKey.generate(name=name, created_by=request.user)
    return JsonResponse({
        "success": True,
        "id": instance.pk,
        "name": instance.name,
        "raw_key": raw_key,  # shown once; never retrievable again after this response
        "key_prefix": instance.key_prefix,
    })


@login_required
@require_POST
def revoke_api_key(request, key_id):
    if not request.user.is_superuser:
        return HttpResponseForbidden()

    key = get_object_or_404(LibrarianAPIKey, pk=key_id)
    if not key.is_active:
        return JsonResponse({"error": "This key is already revoked."}, status=400)

    key.revoke(revoked_by=request.user)
    return JsonResponse({"success": True})


# ── Remote worker admin page (moved off the dashboard — see wagtail_hooks.py) ──

@login_required
def remote_worker_admin(request):
    """
    Standalone, Wagtail-admin-styled page for the API-key-gated remote
    worker: kill-switch toggle, progress since it was last enabled, and
    API key management. Reached from its own sidebar menu item rather
    than living on the dashboard homepage, so a rarely-touched control
    panel doesn't compete for space with the ingestion stats that are
    actually worth seeing at a glance every time an admin logs in.

    Raises PermissionDenied (Wagtail renders this as a proper 403 admin
    page) rather than returning a bare JSON error like the API endpoints
    below it do — this view serves HTML, not JSON, so the failure mode
    should match.
    """
    if not request.user.is_superuser:
        raise PermissionDenied

    _id_placeholder = 999999999
    revoke_key_url_template = reverse(
        "the_librarian:revoke_api_key", args=[_id_placeholder]
    ).replace(str(_id_placeholder), "{id}")

    return render(request, "the_librarian/remote_worker_admin.html", {
        "toggle_url": reverse("the_librarian:toggle_remote_ingestion"),
        "status_url": reverse("the_librarian:remote_ingestion_status"),
        "list_keys_url": reverse("the_librarian:list_api_keys"),
        "create_key_url": reverse("the_librarian:create_api_key"),
        "revoke_key_url_template": revoke_key_url_template,
    })