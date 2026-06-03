# reader/migrations/0007_migrate_to_cryptographic_phone.py
import hmac
import hashlib
from django.db import migrations, models
from django.conf import settings
import encrypted_fields.fields


def migrate_plaintext_to_crypto(apps, schema_editor):
    """
    Data Migration: Hash and encrypt every existing phone_number before the
    plaintext column is dropped.  Uses the historical model state so we can
    still read 'phone_number' even though the live model no longer has it.
    """
    ReaderUser = apps.get_model('reader', 'ReaderUser')

    # Require the dedicated HMAC key — fail loudly if missing so the
    # operator knows exactly what to fix before retrying.
    hash_key = getattr(settings, 'PHONE_HASH_KEY', None)
    if not hash_key:
        raise ValueError(
            "PHONE_HASH_KEY is missing from settings. "
            "Add it (and FIELD_ENCRYPTION_KEYS for django-encrypted-model-fields) "
            "before applying this migration."
        )

    for user in ReaderUser.objects.all():
        raw_number = getattr(user, 'phone_number', None)

        # phone_number was USERNAME_FIELD — every account must have one.
        # Raise immediately rather than silently skipping and then crashing
        # in Phase 4 when the NOT NULL constraint fires on a NULL hash.
        if not raw_number:
            raise ValueError(
                f"User pk={user.pk} has no phone_number. "
                "Resolve orphan accounts before applying this migration."
            )

        normalized = str(raw_number).strip()

        # Compute stable blind index (same key + algorithm used in the model's
        # set_phone / get_by_phone helpers so lookups stay consistent).
        hashed = hmac.new(
            hash_key.encode(),
            normalized.encode(),
            hashlib.sha256,
        ).hexdigest()

        user.phone_number_hash = hashed
        # Assign plaintext; the EncryptedCharField descriptor encrypts on write.
        user.phone_number_encrypted = normalized

        # Save only the two new columns — avoids touching unrelated audit
        # timestamps or triggering unrelated signals.
        user.save(update_fields=['phone_number_hash', 'phone_number_encrypted'])


def rollback_crypto_to_plaintext(apps, schema_editor):
    """
    Rollback: restore plaintext phone numbers if this migration is reversed.
    At this point in the reversal sequence Django has already re-added the
    phone_number column (reverse of RemoveField), so the write is safe.
    """
    ReaderUser = apps.get_model('reader', 'ReaderUser')

    for user in ReaderUser.objects.all():
        # EncryptedCharField descriptor decrypts transparently on read.
        decrypted = user.phone_number_encrypted
        if decrypted:
            user.phone_number = str(decrypted).strip()
            user.save(update_fields=['phone_number'])


class Migration(migrations.Migration):

    dependencies = [
        # Confirmed from `migrate reader 0006_...` output above.
        ('reader', '0006_readeruser_age_declaration_at_and_more'),
    ]

    operations = [

        # ── PHASE 1: EXPAND ──────────────────────────────────────────────────
        # Both columns are nullable here so existing rows don't violate
        # anything before the data migration fills them in.
        migrations.AddField(
            model_name='readeruser',
            name='phone_number_hash',
            field=models.CharField(
                blank=True,
                null=True,
                max_length=64,
                # db_index=True removed: no index needed during data migration,
                # and having it here causes a duplicate _like index in deferred_sql
                # when Phase 4's AlterField also specifies db_index=True.
                verbose_name='Phone Number (Hash)',
            ),
        ),
        migrations.AddField(
            model_name='readeruser',
            name='phone_number_encrypted',
            field=encrypted_fields.fields.EncryptedCharField(
                blank=True,
                null=True,
                max_length=255,
                verbose_name='Phone Number (Encrypted)',
            ),
        ),

        # ── PHASE 2: MIGRATE DATA ─────────────────────────────────────────────
        migrations.RunPython(
            migrate_plaintext_to_crypto,
            rollback_crypto_to_plaintext,
        ),

        # ── PHASE 3: CONTRACT ─────────────────────────────────────────────────
        # Safe to drop now — every row has been copied to the new columns.
        migrations.RemoveField(
            model_name='readeruser',
            name='phone_number',
        ),

        # ── PHASE 4: ENFORCE CONSTRAINTS ──────────────────────────────────────
        # BUG 1 FIX: the original document only altered phone_number_hash.
        # Both columns need their constraints locked down.
        #
        # BUG 1 FIX: Phase 2 now raises on NULL phones instead of silently
        # skipping, so by the time we reach here every row is guaranteed
        # non-null and the NOT NULL constraint cannot fail.
        migrations.AlterField(
            model_name='readeruser',
            name='phone_number_hash',
            field=models.CharField(
                unique=True,
                max_length=64,
                # db_index=True removed: unique=True already creates a unique btree
                # index, which is sufficient for all hash lookups. Including db_index
                # here also queues a duplicate _like index into deferred_sql.
                verbose_name='Phone Number (Hash)',
            ),
        ),
        # BUG 3 FIX: the original document forgot this AlterField entirely.
        migrations.AlterField(
            model_name='readeruser',
            name='phone_number_encrypted',
            field=encrypted_fields.fields.EncryptedCharField(
                max_length=255,
                verbose_name='Phone Number (Encrypted)',
            ),
        ),
    ]