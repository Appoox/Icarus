from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect
from .forms import PostboxForm


@login_required
def postbox_page(request):
    if request.method == 'POST':
        form = PostboxForm(request.POST)
        if form.is_valid():
            postbox = form.save(commit=False)
            postbox.user = request.user
            postbox.save()
            messages.success(
                request,
                'നിങ്ങളുടെ അഭിപ്രായത്തിന് നന്ദി',
            )
            return redirect('postbox:page')
    else:
        # Capture where the user came from (explicit param takes priority)
        page_ctx = request.GET.get('from', '').strip()
        if not page_ctx:
            referer = request.META.get('HTTP_REFERER', '')
            if referer:
                page_ctx = referer[:500]

        form = PostboxForm(initial={
            'feedback_type': 'general',
            'page_context': page_ctx,
        })

    previous = (
        request.user.feedback_submissions
        .order_by('-submitted_at')[:5]
    )

    return render(request, 'postbox/postbox_page.html', {
        'form':     form,
        'previous': previous,
    })