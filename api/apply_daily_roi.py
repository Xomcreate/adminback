# api/management/commands/apply_daily_roi.py
#
# Run this daily via cron or Celery beat:
#   python manage.py apply_daily_roi
#
# Cron example (runs every day at midnight):
#   0 0 * * * /path/to/venv/bin/python /path/to/manage.py apply_daily_roi

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from datetime import timedelta
from api.models import Investment, Investor


class Command(BaseCommand):
    help = "Apply daily ROI to all active investments and credit expired profits to wallet balance"

    def handle(self, *args, **options):
        now               = timezone.now()
        expiry_threshold  = now - timedelta(days=30)

        roi_updated  = 0
        expired      = 0
        balance_credited = 0

        # ── Step 1: Find investments that expire today ────────────────────────
        # These are active investments that have hit or passed 30 days.
        # We credit their FULL current_profit (including today's gain) to the
        # investor's balance before marking them inactive.
        expiring_investments = Investment.objects.filter(
            active=True,
            created_at__lte=expiry_threshold
        ).select_related("investor")

        for investment in expiring_investments:
            with transaction.atomic():
                # Apply one final day of ROI before expiring
                daily_gain = investment.amount * (investment.daily_roi / 100)
                investment.current_profit += daily_gain

                # Credit total profit to investor's wallet balance
                investor = investment.investor
                investor.balance += investment.current_profit
                investor.save(update_fields=["balance"])

                # Mark investment as expired
                investment.active = False
                investment.save(update_fields=["current_profit", "active"])

                expired          += 1
                balance_credited += 1

                self.stdout.write(
                    f"  💰 Expired: {investor.name} | "
                    f"Profit ${float(investment.current_profit):.2f} credited to balance"
                )

        # ── Step 2: Apply daily ROI to remaining active investments ───────────
        active_investments = Investment.objects.filter(active=True).select_related("investor")

        for investment in active_investments:
            with transaction.atomic():
                daily_gain = investment.amount * (investment.daily_roi / 100)
                investment.current_profit += daily_gain
                investment.save(update_fields=["current_profit"])
                roi_updated += 1

        # ── Summary ───────────────────────────────────────────────────────────
        self.stdout.write(
            self.style.SUCCESS(
                f"\n[{now.date()}] Daily ROI applied to {roi_updated} active investment(s). "
                f"{expired} investment(s) expired and profits credited to {balance_credited} wallet(s)."
            )
        )