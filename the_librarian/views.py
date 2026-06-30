from functools import wraps
from pathlib import Path

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, Http404, FileResponse
from django.conf import settings
from django.urls import reverse
from django.apps import apps
from django.views.decorators.http import require_GET, require_POST

import logging

from the_librarian.models import (
    ArchiveDocument, DocumentChunk,
    ArchiveIssue, MONTH_CHOICES,
)
from the_librarian.services.search import (
    search_similar,
    search_keyword,
    search_hybrid,
    search_by_document,
)

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
                        # Dynamically fetches the proper URL path (e.g. /issues/topics/<slug>/)
                        r["url"] = reverse('topic_detail', args=[topic_map[tid].slug])
        except Exception as e:
            logger.warning(f"Could not resolve topic URLs: {e}")

    return results


# ── Access-control decorator ──────────────────────────────────────────────

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

def viewer_view(request, document_id):
    """Display an ingested ArchiveDocument PDF via ViewerJS."""
    try:
        doc = ArchiveDocument.objects.get(pk=document_id)
    except ArchiveDocument.DoesNotExist:
        raise Http404("Document not found")

    page = int(request.GET.get("page", 1))

    from django.urls import reverse
    pdf_url = reverse("the_librarian:serve_pdf", args=[document_id])

    return render(request, "the_librarian/viewer.html", {
        "document": doc,
        "page": page,
        "pdf_url": pdf_url,
    })


def serve_pdf(request, document_id):
    """
    Serve an ingested ArchiveDocument PDF.

    Resolution order:
    1. ARCHIVE_DIR / doc.file_path   — filesystem-ingested PDFs
    2. MEDIA_ROOT  / doc.file_path   — ArchiveIssue-uploaded PDFs
                                       (file_path stored as MEDIA_ROOT-relative)
    """
    try:
        doc = ArchiveDocument.objects.get(pk=document_id)
    except ArchiveDocument.DoesNotExist:
        raise Http404("Document not found")

    # Try ARCHIVE_DIR first (legacy filesystem-ingested PDFs)
    pdf_path = Path(settings.ARCHIVE_DIR) / doc.file_path
    if not pdf_path.exists():
        # Fallback: MEDIA_ROOT-relative path (ArchiveIssue uploads)
        pdf_path = Path(settings.MEDIA_ROOT) / doc.file_path

    if not pdf_path.exists():
        raise Http404("PDF file not found on disk")

    return FileResponse(
        open(pdf_path, "rb"),
        content_type="application/pdf",
        filename=doc.filename,
    )


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


@require_GET
def magazine_viewer(request, issue_id):
    """Display a ArchiveIssue PDF via ViewerJS."""
    issue = get_object_or_404(ArchiveIssue, pk=issue_id)

    from django.urls import reverse
    pdf_url = reverse("the_librarian:serve_magazine_pdf", args=[issue_id])

    return render(request, "the_librarian/viewer.html", {
        "document": {
            "filename":    issue.title,
            "total_pages": "?",
        },
        "page":    int(request.GET.get("page", 1)),
        "pdf_url": pdf_url,
    })


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

@require_GET
def archive_viewer(request, filename):
    """View a raw filesystem PDF from ARCHIVE_DIR via ViewerJS."""
    from django.urls import reverse
    pdf_url = reverse("the_librarian:archive_download", args=[filename])

    return render(request, "the_librarian/viewer.html", {
        "document": {"filename": filename, "total_pages": "?"},
        "page": 1,
        "pdf_url": pdf_url,
    })


@require_GET
def archive_download(request, filename):
    """Serve a raw PDF from ARCHIVE_DIR by filename."""
    import os
    safe_filename = os.path.basename(filename)
    pdf_path = Path(settings.ARCHIVE_DIR) / safe_filename

    if not pdf_path.exists() or not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
        raise Http404("PDF file not found on disk")

    return FileResponse(
        open(pdf_path, "rb"),
        content_type="application/pdf",
        filename=safe_filename,
    )


# ── Admin ingestion triggers ──────────────────────────────────────────────

@superuser_required
@require_POST
def trigger_ingestion(request):
    """
    Admin-only: queue an async Django-Q2 ingestion task for one ArchiveIssue.

    POST params:
        archive_issue_pk  — PK of the ArchiveIssue to ingest (required)
        force             — 'true' to re-ingest even if already processed

    Returns:
        JSON with task_id on success so the frontend can poll task_status_view.
    """
    from the_librarian.services.ingestion import clear_stop_signal

    archive_issue_pk = request.POST.get("archive_issue_pk", "").strip()
    force = request.POST.get("force", "false").lower() == "true"

    # Clear any previous stop signal at the start of a fresh batch
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
        # django_q not installed — fall back to synchronous ingestion
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
    Admin-only: return ArchiveIssue objects not yet ingested.

    Query params:
        force — 'true' to return all ArchiveIssues (including already-ingested ones)

    Returns:
        JSON: { success: true, pending: [{ pk, title }, ...] }
    """
    force = request.GET.get("force", "false").lower() == "true"

    if force:
        issues_qs = ArchiveIssue.objects.all()
    else:
        # Only issues that have no linked ArchiveDocument (not yet ingested)
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

    Django-Q2 stores completed tasks (successful and failed alike) in the
    django_q_task table via the Task model.  If the task is not yet in that
    table it is still queued or being processed — return 'pending'.

    Returns JSON:
        { status: 'pending' | 'success' | 'failed', result: str }
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
            # Task hasn't completed yet (still queued or being processed)
            return JsonResponse({"status": "pending"})
    except ImportError:
        return JsonResponse({
            "status": "unavailable",
            "message": "django_q is not installed",
        })