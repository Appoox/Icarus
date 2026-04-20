from django.core.management.base import BaseCommand
from issue.models import Issue
from literati.models import EditorialBoard, EditorialBoardMember

class Command(BaseCommand):
    help = 'Migrates existing editorial board members from individual issues to the new group-based EditorialBoard model.'

    def handle(self, *args, **options):
        issues = Issue.objects.all()
        migrated_count = 0
        skipped_count = 0

        for issue in issues:
            old_rels = issue.editorial_board_relationship.all().order_by('sort_order')
            if not old_rels.exists():
                skipped_count += 1
                continue

            # Create a unique signature for this board configuration
            # Format: [(editor_id, role), (editor_id, role), ...]
            config_signature = tuple((rel.editor_id, rel.role) for rel in old_rels)

            # Try to find an existing board with this exact configuration
            matching_board = None
            for board in EditorialBoard.objects.all():
                board_members = board.members.all().order_by('sort_order')
                if len(board_members) == len(config_signature):
                    board_signature = tuple((m.editor_id, m.role) for m in board_members)
                    if board_signature == config_signature:
                        matching_board = board
                        break

            if not matching_board:
                # Create a new board
                board_name = f"Editorial Board ({issue.title})"
                matching_board = EditorialBoard.objects.create(name=board_name)
                
                # Copy members
                for i, rel in enumerate(old_rels):
                    EditorialBoardMember.objects.create(
                        board=matching_board,
                        editor_id=rel.editor_id,
                        role=rel.role,
                        sort_order=rel.sort_order if rel.sort_order is not None else i
                    )
                self.stdout.write(self.style.SUCCESS(f"Created new board: '{board_name}'"))

            # Link the issue to the board
            issue.editorial_board = matching_board
            issue.save()
            migrated_count += 1
            self.stdout.write(f"Linked issue '{issue.title}' to board '{matching_board.name}'")

        self.stdout.write(self.style.SUCCESS(f"Migration complete. Migrated: {migrated_count}, Skipped (no board): {skipped_count}"))
