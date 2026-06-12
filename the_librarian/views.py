from functools import wraps
from pathlib import Path

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, Http404, FileResponse
from django.conf import settings
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

    return JsonResponse({"query": query, "results": results})


# ── ArchiveDocument ViewerJS display ─────────────────────────────────────

def viewer_view(request, document_id):
    """
    Display an ingested ArchiveDocument PDF via ViewerJS.
    URL: /librarian/viewer/<document_id>/?page=3
    """
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
    """Serve an ingested ArchiveDocument PDF from ARCHIVE_DIR."""
    try:
        doc = ArchiveDocument.objects.get(pk=document_id)
    except ArchiveDocument.DoesNotExist:
        raise Http404("Document not found")

    pdf_path = Path(settings.ARCHIVE_DIR) / doc.file_path
    if not pdf_path.exists():
        raise Http404("PDF file not found on disk")

    return FileResponse(
        open(pdf_path, "rb"),
        content_type="application/pdf",
        filename=doc.filename,
    )


# ── Archive Issue views ──────────────────────────────────────────────────

@require_GET
def archive_list(request):
    """
    Public archive grid: filterable, sortable Archive Issue cards.

    GET params:
        year    - filter by year (int)
        month   - filter by month (int 1-12)
        volume  - filter by volume number (int)
        sort    - 'newest' (default) | 'oldest' | 'volume'
    """
    # ── Parse filters ──
    def _int_or_none(key):
        try:
            return int(request.GET[key])
        except (KeyError, ValueError, TypeError):
            return None

    selected_year   = _int_or_none('year')
    selected_month  = _int_or_none('month')
    selected_volume = _int_or_none('volume')
    sort            = request.GET.get('sort', 'newest')

    # ── Base queryset ──
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
    else:  # newest (default)
        issues = issues.order_by('-year', '-month', '-issue_number')

    # ── Filter option lists (always from full set, not filtered) ──
    all_issues  = ArchiveIssue.objects.all()
    all_years   = (
        all_issues
        .values_list('year', flat=True)
        .distinct()
        .order_by('-year')
    )
    all_volumes = (
        all_issues
        .values_list('volume', flat=True)
        .distinct()
        .order_by('volume')
    )

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
    """
    Display a ArchiveIssue PDF via ViewerJS.
    URL: /librarian/magazine/<issue_id>/?page=N
    """
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
    """Force-download the PDF for a ArchiveIssue (Content-Disposition: attachment)."""
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


# ── Direct archive filesystem access (kept for ingestion dashboard) ──────

@require_GET
def archive_viewer(request, filename):
    """
    View a raw filesystem PDF from ARCHIVE_DIR via ViewerJS.
    Used by the ingestion dashboard; not shown on the public archive page.
    """
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
    """Admin-only: trigger archive ingestion (one file at a time or all)."""
    from the_librarian.services.ingestion import ingest_archive, clear_stop_signal

    if not request.POST.get("filename"):
        clear_stop_signal()

    force    = request.POST.get("force", "false").lower() == "true"
    filename = request.POST.get("filename")

    try:
        results = ingest_archive(force=force, filename=filename)
        return JsonResponse({
            "success":   True,
            "processed": len(results["processed"]),
            "skipped":   len(results["skipped"]),
            "errors":    len(results["errors"]),
            "details":   results,
        })
    except Exception as e:
        logger.exception("Ingestion failed")
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@superuser_required
@require_POST
def stop_ingestion(request):
    """Admin-only: signal a running ingestion to stop after the current file."""
    from the_librarian.services.ingestion import request_stop
    request_stop()
    return JsonResponse({"success": True, "message": "Stop requested"})


@superuser_required
@require_GET
def get_pending_pdfs_view(request):
    """Admin-only: return the list of PDFs not yet ingested."""
    from the_librarian.services.ingestion import get_pending_pdfs
    force   = request.GET.get("force", "false").lower() == "true"
    pending = get_pending_pdfs(force=force)
    return JsonResponse({"success": True, "pending": pending})