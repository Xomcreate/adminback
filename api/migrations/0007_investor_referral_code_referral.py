import uuid
from django.db import migrations, models
import django.db.models.deletion


def generate_referral_codes(apps, schema_editor):
    """Backfill empty referral codes."""
    Investor = apps.get_model("api", "Investor")
    for investor in Investor.objects.filter(referral_code=""):
        code = uuid.uuid4().hex[:8].upper()
        while Investor.objects.filter(referral_code=code).exists():
            code = uuid.uuid4().hex[:8].upper()
        investor.referral_code = code
        investor.save(update_fields=["referral_code"])


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0006_investment_plan_investment_status_investment_type_and_more"),
    ]

    operations = [
        # Step 1: add the column properly (updates both DB and Django's state)
        migrations.AddField(
            model_name="investor",
            name="referral_code",
            field=models.CharField(max_length=20, default="", blank=True),
        ),

        # Step 2: backfill empty codes
        migrations.RunPython(
            generate_referral_codes,
            reverse_code=migrations.RunPython.noop,
        ),

        # Step 3: now enforce uniqueness, after all rows have real codes
        migrations.AlterField(
            model_name="investor",
            name="referral_code",
            field=models.CharField(max_length=20, unique=True, blank=True),
        ),

        # Step 4: create Referral table
        migrations.CreateModel(
            name="Referral",
            fields=[
                ("id",             models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("referred_name",  models.CharField(blank=True, default="", max_length=150)),
                ("referred_email", models.EmailField(blank=True, default="")),
                ("referrer_name",  models.CharField(blank=True, default="", max_length=150)),
                ("status",         models.CharField(
                                       choices=[("pending","Pending"),("active","Active"),("inactive","Inactive"),("expired","Expired")],
                                       default="pending", max_length=10,
                                   )),
                ("approved",       models.BooleanField(default=False)),
                ("commission",     models.DecimalField(decimal_places=2, default=50.0, max_digits=10)),
                ("created_at",     models.DateTimeField(auto_now_add=True)),
                ("referrer",       models.ForeignKey(
                                       on_delete=django.db.models.deletion.CASCADE,
                                       related_name="referrals_made",
                                       to="api.investor",
                                   )),
                ("referred_user",  models.OneToOneField(
                                       blank=True, null=True,
                                       on_delete=django.db.models.deletion.SET_NULL,
                                       related_name="referral_source",
                                       to="api.investor",
                                   )),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]