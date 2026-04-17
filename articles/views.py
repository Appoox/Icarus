from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.db.models import F
from .models import Article

@require_POST
def track_read_fully(request):
    article_id = request.POST.get('article_id')
    if not article_id:
        return JsonResponse({'status': 'error', 'message': 'Missing article_id'}, status=400)

    # Analytics: Only count if NOT staff/superuser
    if not (request.user.is_superuser or request.user.is_staff):
        read_articles = request.session.get('read_articles_fully', [])
        article_id = int(article_id)
        
        if article_id not in read_articles:
            Article.objects.filter(pk=article_id).update(read_fully_count=F('read_fully_count') + 1)
            read_articles.append(article_id)
            request.session['read_articles_fully'] = read_articles
            return JsonResponse({'status': 'success', 'message': 'Read count incremented'})
        
        return JsonResponse({'status': 'ignored', 'message': 'Already counted in this session'})
    
    return JsonResponse({'status': 'ignored', 'message': 'Staff views not counted'})

from django.shortcuts import render
from taggit.models import Tag

def tag_detail(request, tag):
    tag_obj = Tag.objects.filter(name=tag).first()
    if tag_obj:
        articles = Article.objects.live().filter(tags__name=tag_obj.name).order_by('-first_published_at')
    else:
        articles = []
    
    return render(request, 'articles/tag_detail.html', {
        'tag_name': tag_obj.name if tag_obj else tag,
        'articles': articles,
    })
