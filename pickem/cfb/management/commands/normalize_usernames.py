from collections import defaultdict

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Normalize usernames and emails to lowercase to prepare for "
        "case-insensitive authentication. Run without --commit first to "
        "inspect the changes."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Persist the changes. Without this flag the command runs in dry-run mode.",
        )

    def handle(self, *args, **options):
        commit: bool = options["commit"]
        User = get_user_model()

        normalized_index: dict[str, list[tuple[int, str]]] = defaultdict(list)
        candidates: list[tuple[int, str, str, str]] = []

        for user in User.objects.all():
            normalized_username = user.username.lower()
            normalized_email = (user.email or "").lower()

            normalized_index[normalized_username].append((user.pk, user.username))

            if user.username != normalized_username or user.email != normalized_email:
                candidates.append(
                    (user.pk, user.username, normalized_username, normalized_email)
                )

        conflicts = {
            key: value for key, value in normalized_index.items() if len(value) > 1
        }

        if conflicts:
            self.stdout.write(self.style.WARNING("Conflicting usernames detected:"))
            for username, entries in conflicts.items():
                self.stdout.write(f"  '{username}': {[name for _pk, name in entries]}")
            self.stdout.write(
                self.style.ERROR(
                    "Resolve these duplicates manually (e.g., via the Django admin) "
                    "before running with --commit."
                )
            )
            if commit:
                raise CommandError(
                    "Duplicate usernames would be created; aborting --commit run."
                )

        if not candidates:
            self.stdout.write("No usernames or emails require normalization.")
            return

        if not commit:
            self.stdout.write(self.style.MIGRATE_HEADING("Planned changes:"))
            for pk, current_username, new_username, new_email in candidates:
                self.stdout.write(
                    f"  User #{pk}: username '{current_username}' -> '{new_username}', "
                    f"email -> '{new_email or '[empty]'}'"
                )
            self.stdout.write(
                self.style.NOTICE(
                    "Run with --commit once you have reviewed conflicts and are ready "
                    "to apply these updates."
                )
            )
            return

        for pk, _current_username, new_username, new_email in candidates:
            fields = {"username": new_username}
            if new_email:
                fields["email"] = new_email
            User.objects.filter(pk=pk).update(**fields)

        self.stdout.write(
            self.style.SUCCESS(
                f"Normalized {len(candidates)} user(s). Remember to review audit logs."
            )
        )

