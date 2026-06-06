# api/management/commands/apply_daily_roi.py
#
# Run this daily via cron or Celery beat:
#   python manage.py apply_daily_roi
#
# Cron example (runs every day at midnight UTC):
#   0 0 * * * /path/to/venv/bin/python /path/to/manage.py apply_daily_roi

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from datetime import timedelta
from api.models import Investment, Investor

EXPIRY_DAYS = 120


class Command(BaseCommand):
    help = "Apply 25% daily ROI to all active investments. Mature and credit wallets after 120 days."

    def handle(self, *args, **options):
        now              = timezone.now()
        expiry_threshold = now - timedelta(days=EXPIRY_DAYS)

        roi_updated = 0
        matured     = 0

        # ── Step 1: Mature investments that have hit 120 days ─────────────────
        expiring_investments = Investment.objects.filter(
            active=True,
            approved=True,
            created_at__lte=expiry_threshold,
        ).select_related("investor")

        for investment in expiring_investments:
            with transaction.atomic():
                # Apply one final day of ROI before closing
                daily_gain = investment.amount * (investment.daily_roi / 100)
                investment.current_profit += daily_gain

                payout = investment.amount + investment.current_profit

                investor = Investor.objects.select_for_update().get(
                    pk=investment.investor.pk
                )
                investor.balance += payout
                investor.save(update_fields=["balance"])

                investment.active = False
                investment.save(update_fields=["current_profit", "active"])

                matured += 1

                self.stdout.write(
                    f"  💰 Matured: {investor.name} | Plan: {investment.category} | "
                    f"Principal: ${float(investment.amount):,.2f} | "
                    f"Total Profit: ${float(investment.current_profit):,.2f} | "
                    f"Payout: ${float(payout):,.2f} credited to wallet"
                )

        # ── Step 2: Apply daily 25% ROI to remaining active investments ───────
        active_investments = Investment.objects.filter(
            active=True,
            approved=True,
        ).select_related("investor")

        for investment in active_investments:
            with transaction.atomic():
                daily_gain = investment.amount * (investment.daily_roi / 100)
                investment.current_profit += daily_gain
                investment.save(update_fields=["current_profit"])
                roi_updated += 1

                self.stdout.write(
                    f"  📈 ROI: {investment.investor.name} | {investment.category} | "
                    f"+${float(daily_gain):,.2f} | "
                    f"Total so far: ${float(investment.current_profit):,.2f}"
                )

        # ── Summary ───────────────────────────────────────────────────────────
        self.stdout.write(
            self.style.SUCCESS(
                f"\n[{now.date()}] Done — {roi_updated} investment(s) updated with 25% daily ROI. "
                f"{matured} investment(s) matured after 120 days and profits credited to wallets."
            )
        )