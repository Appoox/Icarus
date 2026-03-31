from django.core.management.base import BaseCommand
from articles.models import Article
from literati.models import Literati
from the_librarian.services.indexing import index_article, index_author

class Command(BaseCommand):
    help = 'Performs initial mass-indexing of all Articles and Literati profiles into the Librarian search.'

    def handle(self, *args, **options):
        # 1. Index Articles
        articles = Article.objects.live()
        self.stdout.write(f"Found {articles.count()} live articles to index...")
        for article in articles:
            try:
                chunks = index_article(article)
                self.stdout.write(self.style.SUCCESS(f"  ✓ Indexed '{article.title}' ({chunks} chunks)"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  ✗ Failed '{article.title}': {e}"))

        # 2. Index Literati (Authors)
        authors = Literati.objects.live()
        self.stdout.write(f"\nFound {authors.count()} author profiles to index...")
        for author in authors:
            try:
                chunks = index_author(author)
                self.stdout.write(self.style.SUCCESS(f"  ✓ Indexed '{author.title}' ({chunks} chunks)"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  ✗ Failed '{author.title}': {e}"))

        self.stdout.write(self.style.SUCCESS("\nMass indexing complete!"))
