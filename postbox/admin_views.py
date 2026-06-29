from urllib.parse import urlencode

from django.http import HttpResponseForbidden
from django.shortcuts import render, get_object_or_404, redirect
from django.core.paginator import Paginator
from django.urls import reverse

from .models import Postbox


def _is_staff(request):
    return request.user.is_authenticated and (
        request.user.is_staff or request.user.is_superuser
    )


_SORTABLE = ('feedback_type', 'rating', 'submitted_at', 'is_reviewed', 'user__name')
_VALID_ORDERINGS = {f for f in _SORTABLE} | {f'-{f}' for f in _SORTABLE}


def _sort_info(filters, ordering):
    """For each sortable field, compute the toggle URL and direction indicator."""
    base = {k: v for k, v in filters.items() if v}
    out  = {}
    for field in _SORTABLE:
        p = dict(base)
        if ordering == field:
            p['ordering'] = f'-{field}'
            icon, active = '↑', True
        elif ordering == f'-{field}':
            p['ordering'] = field
            icon, active = '↓', True
        else:
            p['ordering'] = field
            icon, active = '', False
        out[field] = {'url': '?' + urlencode(p), 'icon': icon, 'active': active}
    return out


def _paged_url(filters, ordering, n):
    p = {k: v for k, v in filters.items() if v}
    p['ordering'] = ordering
    if n > 1:
        p['page'] = n
    return '?' + urlencode(p)


def postbox_index(request):
    if not _is_staff(request):
        return HttpResponseForbidden()

    qs = Postbox.objects.select_related('user').all()

    # ── Filters ──────────────────────────────────────────────────────────
    f_type     = request.GET.get('type',     '').strip()
    f_reviewed = request.GET.get('reviewed', '').strip()
    f_rating   = request.GET.get('rating',   '').strip()

    if f_type:
        qs = qs.filter(feedback_type=f_type)
    if f_reviewed == '0':
        qs = qs.filter(is_reviewed=False)
    elif f_reviewed == '1':
        qs = qs.filter(is_reviewed=True)
    if f_rating.isdigit():
        qs = qs.filter(rating=int(f_rating))

    # ── Sorting ───────────────────────────────────────────────────────────
    ordering = request.GET.get('ordering', '-submitted_at')
    if ordering not in _VALID_ORDERINGS:
        ordering = '-submitted_at'
    qs = qs.order_by(ordering)

    # ── Pagination ────────────────────────────────────────────────────────
    paginator = Paginator(qs, 20)
    page_obj  = paginator.get_page(request.GET.get('page'))
    filters   = {'type': f_type, 'reviewed': f_reviewed, 'rating': f_rating}

    return render(request, 'postbox/wagtailadmin/index.html', {
        'page_obj':         page_obj,
        'total':            paginator.count,
        'unreviewed_total': Postbox.objects.filter(is_reviewed=False).count(),
        'ordering':         ordering,
        'filters':          filters,
        'feedback_types':   Postbox.FEEDBACK_TYPES,
        'sort_info':        _sort_info(filters, ordering),
        'has_filters':      any(filters.values()),
        'clear_url':        f'?ordering={ordering}',
        'prev_url':         _paged_url(filters, ordering, page_obj.previous_page_number) if page_obj.has_previous() else None,
        'next_url':         _paged_url(filters, ordering, page_obj.next_page_number)     if page_obj.has_next()     else None,
    })


def postbox_detail(request, pk):
    if not _is_staff(request):
        return HttpResponseForbidden()

    obj = get_object_or_404(Postbox.objects.select_related('user'), pk=pk)

    if request.method == 'POST':
        obj.admin_notes = request.POST.get('admin_notes', '').strip()
        obj.is_reviewed = request.POST.get('is_reviewed') == '1'
        obj.save(update_fields=['admin_notes', 'is_reviewed'])
        return redirect(reverse('icarus_postbox_detail', args=[pk]))

    return render(request, 'postbox/wagtailadmin/detail.html', {
        'obj':       obj,
        'index_url': reverse('icarus_postbox_index'),
    })