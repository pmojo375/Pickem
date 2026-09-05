from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cfb', '0025_game_possession'),
    ]

    operations = [
        migrations.AddField(
            model_name='leaguerules',
            name='pick_reminder_emails_enabled',
            field=models.BooleanField(
                default=False,
                help_text='Email members who have not finished their picks before the week\'s first kickoff',
            ),
        ),
    ]
