from django.conf import settings
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('postbox', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PostboxNotification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('feedback',    models.CharField(default='', max_length=255)),
                ('is_read',    models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('postbox', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='notifications',
                    to='postbox.postbox',
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='postbox_notifications',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Postbox Notification',
                'verbose_name_plural': 'Postbox Notifications',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='PostboxNotificationPreference',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('receive_notifications', models.BooleanField(default=True)),
                ('user', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='postbox_notification_preference',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
        ),
    ]