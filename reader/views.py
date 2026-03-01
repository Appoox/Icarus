from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages

from .forms import ReaderSignupForm
from .models import Reader


def reader_signup(request):
    """Register a new reader account."""
    if request.user.is_authenticated:
        return redirect('reader_profile')

    if request.method == 'POST':
        form = ReaderSignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, 'Welcome! Your account has been created.')
            return redirect('reader_profile')
    else:
        form = ReaderSignupForm()

    return render(request, 'reader/signup.html', {'form': form})


@login_required(login_url='/reader/login/')
def reader_profile(request):
    """Display reader profile with reading history and subscription info."""
    try:
        reader = request.user.reader
    except Reader.DoesNotExist:
        # If a logged-in user has no reader profile, create one
        reader = Reader.objects.create(
            user=request.user,
            name=request.user.get_full_name() or request.user.username,
            email=request.user.email or '',
        )

    context = {
        'reader': reader,
        'read_articles': reader.read_articles.all().order_by('-first_published_at')[:20],
        'interested_topics': reader.interested_topics.all(),
    }
    return render(request, 'reader/profile.html', context)


def reader_logout(request):
    """Log the user out and redirect to home."""
    logout(request)
    messages.info(request, 'You have been signed out.')
    return redirect('/')
