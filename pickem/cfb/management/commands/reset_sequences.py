"""
Reset PostgreSQL primary-key sequences to match MAX(id) on each table.

Duplicate key errors like:
  IntegrityError: duplicate key value violates unique constraint "..._pkey"
happen when rows were inserted with explicit IDs (imports, restores, COPY)
and the sequence that generates new IDs was left behind.

Usage:
  python manage.py reset_sequences
  python manage.py reset_sequences --dry-run
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connections


SEQUENCE_QUERY = """
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    a.attname AS column_name,
    pg_get_serial_sequence(
        format('%I.%I', n.nspname, c.relname),
        a.attname
    ) AS sequence_name
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
WHERE c.relkind IN ('r', 'p')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND a.attnum > 0
  AND NOT a.attisdropped
  AND pg_get_serial_sequence(
        format('%I.%I', n.nspname, c.relname),
        a.attname
      ) IS NOT NULL
ORDER BY 1, 2, 3;
"""


class Command(BaseCommand):
    help = "Reset PostgreSQL sequences so new inserts use MAX(id)+1 (fixes duplicate pkey errors)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show sequences that are out of sync without changing them",
        )
        parser.add_argument(
            "--database",
            default="default",
            help="Database alias to use (default: default)",
        )

    def handle(self, *args, **options):
        using = options["database"]
        dry_run = options["dry_run"]
        connection = connections[using]

        if connection.vendor != "postgresql":
            raise CommandError(
                f"This command only works with PostgreSQL (got {connection.vendor})."
            )

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No sequences will be changed\n"))

        reset_count = 0
        already_ok = 0

        with connection.cursor() as cursor:
            cursor.execute(SEQUENCE_QUERY)
            sequences = cursor.fetchall()

            if not sequences:
                self.stdout.write("No sequences found.")
                return

            for schema_name, table_name, column_name, sequence_name in sequences:
                quoted_table = "{}.{}".format(
                    connection.ops.quote_name(schema_name),
                    connection.ops.quote_name(table_name),
                )
                quoted_col = connection.ops.quote_name(column_name)
                cursor.execute(f"SELECT MAX({quoted_col}) FROM {quoted_table}")
                max_id = cursor.fetchone()[0]

                seq_schema, _, seq_name = sequence_name.partition(".")
                quoted_seq = "{}.{}".format(
                    connection.ops.quote_name(seq_schema),
                    connection.ops.quote_name(seq_name),
                )
                cursor.execute(f"SELECT last_value, is_called FROM {quoted_seq}")
                last_value, is_called = cursor.fetchone()
                next_id = last_value + 1 if is_called else last_value
                expected_next = (max_id + 1) if max_id is not None else 1

                if next_id == expected_next:
                    already_ok += 1
                    continue

                self.stdout.write(
                    f"  {sequence_name}: next would be {next_id}, "
                    f"should be {expected_next} (max {column_name} on {quoted_table} is {max_id})"
                )

                if not dry_run:
                    cursor.execute(
                        "SELECT setval(%s, COALESCE(%s, 1), %s)",
                        [sequence_name, max_id or 1, max_id is not None],
                    )
                reset_count += 1

        if reset_count == 0:
            self.stdout.write(
                self.style.SUCCESS(f"All {already_ok} sequence(s) already match MAX(id).")
            )
            return

        verb = "Would reset" if dry_run else "Reset"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {reset_count} sequence(s). {already_ok} already in sync."
            )
        )
        if dry_run:
            self.stdout.write("Re-run without --dry-run to apply.")
        else:
            self.stdout.write("New inserts should no longer hit duplicate primary key errors.")
