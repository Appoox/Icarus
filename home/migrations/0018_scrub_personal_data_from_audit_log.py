"""
Remove readers' and authors' personal data from existing audit-log rows.

ReaderUser was audited with no exclusions, so every change to a reader
logged the decrypted phone number, email, address, IPs and password hash,
readable by every holder of home.view_auditlog. New rows exclude those
fields (reader.models.READER_AUDIT_EXCLUDED_FIELDS, literati.apps.
LITERATI_AUDIT_EXCLUDED_FIELDS). This removes the same keys from the
rows already written, strips phone numbers from reader object names, and
deletes update rows whose only changes were personal data.

The field lists are copied here on purpose: a migration must not change
meaning when app code changes later.
"""
import json
import re

from django.db import migrations

READER_FIELDS = {
    "password", "last_login",
    "phone_number_hash", "phone_number_encrypted", "email",
    "registration_ip", "last_login_ip",
    "gender", "gender_other", "birth_year",
    "address_line_1", "address_line_2", "city", "post_office", "pincode",
    "district", "state",
    "care_of_name", "care_of_number", "care_of_district", "care_of_meghala",
    "care_of_unit", "delivery_notes",
}
LITERATI_FIELDS = {
    "phone_number", "email",
    "address_line_1", "address_line_2", "city", "post_office", "pincode",
    "district", "state",
}
PHONE = re.compile(r"\+\d{7,15}")
UPDATE = 1  # auditlog LogEntry.Action.UPDATE


def _strip(changes, fields):
    """Return (new_changes, was_changed) for a dict or a JSON string."""
    as_text = isinstance(changes, str)
    data = changes
    if as_text:
        try:
            data = json.loads(changes)
        except ValueError:
            return changes, False
    if not isinstance(data, dict):
        return changes, False
    kept = {k: v for k, v in data.items() if k not in fields}
    if kept == data:
        return changes, False
    return (json.dumps(kept) if as_text else kept), True


def scrub(apps, schema_editor):
    LogEntry = apps.get_model("home", "CustomLogEntry")
    ContentType = apps.get_model("contenttypes", "ContentType")

    for app_label, model, fields in (
        ("reader", "readeruser", READER_FIELDS),
        ("literati", "literati", LITERATI_FIELDS),
    ):
        ct = ContentType.objects.filter(app_label=app_label, model=model).first()
        if ct is None:
            continue
        to_update, to_delete = [], []
        rows = LogEntry.objects.filter(content_type=ct).only(
            "id", "action", "changes", "changes_text", "object_repr", "serialized_data"
        )
        for entry in rows.iterator(chunk_size=500):
            touched = False
            entry.changes, c1 = _strip(entry.changes, fields)
            entry.changes_text, c2 = _strip(entry.changes_text, fields)
            touched = c1 or c2
            if entry.serialized_data is not None:
                entry.serialized_data = None
                touched = True
            if app_label == "reader" and entry.object_repr:
                cleaned = PHONE.sub("[phone removed]", entry.object_repr)
                if cleaned != entry.object_repr:
                    entry.object_repr = cleaned
                    touched = True
            if entry.action == UPDATE and (c1 or c2) and not entry.changes:
                to_delete.append(entry.pk)  # the update only touched personal data
            elif touched:
                to_update.append(entry)
            if len(to_update) >= 500:
                LogEntry.objects.bulk_update(
                    to_update, ["changes", "changes_text", "object_repr", "serialized_data"]
                )
                to_update = []
        if to_update:
            LogEntry.objects.bulk_update(
                to_update, ["changes", "changes_text", "object_repr", "serialized_data"]
            )
        for start in range(0, len(to_delete), 1000):
            LogEntry.objects.filter(pk__in=to_delete[start:start + 1000]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("home", "0017_grant_report_access_to_manual_staff"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(scrub, migrations.RunPython.noop),
    ]
