from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from allauth.account.models import EmailAddress

User = get_user_model()


class Command(BaseCommand):
    help = (
        "Create allauth EmailAddress rows from User.email and mark them verified. "
        "Needed for accounts created in admin so password login does not loop on "
        "email confirmation. Run without --commit first to inspect."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Persist the changes. Without this flag the command is a dry run.",
        )

    def handle(self, *args, **options):
        commit = options["commit"]
        created = 0
        verified = 0
        skipped = 0

        for user in User.objects.exclude(email="").exclude(email__isnull=True).order_by("pk"):
            email = user.email.strip().lower()
            if not email:
                skipped += 1
                continue

            taken = (
                EmailAddress.objects.filter(email__iexact=email)
                .exclude(user=user)
                .exists()
            )
            if taken:
                self.stdout.write(
                    self.style.WARNING(
                        f"Skip {user.username}: {email} is already on another account."
                    )
                )
                skipped += 1
                continue

            address = EmailAddress.objects.filter(user=user, email__iexact=email).first()
            if address and address.verified and address.primary:
                continue

            if not commit:
                action = "create" if address is None else "verify"
                self.stdout.write(f"  {action} {user.username} <{email}>")
                if address is None:
                    created += 1
                else:
                    verified += 1
                continue

            if address is None:
                EmailAddress.objects.create(
                    user=user,
                    email=email,
                    verified=True,
                    primary=True,
                )
                created += 1
            else:
                address.email = email
                address.verified = True
                address.primary = True
                address.save(update_fields=["email", "verified", "primary"])
                verified += 1

        prefix = "" if commit else "Would "
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}create {created} and verify {verified} email(s); skipped {skipped}."
            )
        )
        if not commit and (created or verified):
            self.stdout.write(
                self.style.NOTICE("Run with --commit to apply these updates.")
            )
