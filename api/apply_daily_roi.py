import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from api.models import Investment, Investor

logger = logging.getLogger(__name__)

EXPIRY_DAYS = 120


def apply_daily_roi():
    now              = timezone.now()
    expiry_threshold = now - timedelta(days=EXPIRY_DAYS)
    matured          = 0
    roi_updated      = 0

    expiring = Investment.objects.filter(
        active=True,
        approved=True,
        created_at__lte=expiry_threshold,
    ).select_related("investor")

    for investment in expiring:
        with transaction.atomic():
            investor = Investor.objects.select_for_update().get(pk=investment.investor.pk)

            daily_gain = investment.amount * (investment.daily_roi / 100)
            investment.current_profit += daily_gain

            payout = investment.amount + investment.current_profit

            investment.active = False
            investment.save(update_fields=["current_profit", "active"])

            investor.balance += payout
            investor.save(update_fields=["balance"])

            matured += 1
            logger.info(
                f"[MATURED] {investor.name} | "
                f"Category: {investment.category} | "
                f"Principal: ${float(investment.amount):.2f} | "
                f"Total Profit: ${float(investment.current_profit):.2f} | "
                f"Payout: ${float(payout):.2f} | "
                f"New Balance: ${float(investor.balance):.2f}"
            )

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
            logger.info(
                f"[ROI] {investment.investor.name} | "
                f"{investment.category} | "
                f"+${float(daily_gain):.2f} daily gain | "
                f"Total profit so far: ${float(investment.current_profit):.2f}"
            )

    logger.info(
        f"[{now.date()}] ROI job complete — "
        f"{roi_updated} investment(s) updated, {matured} matured and credited."
    )
    return f"Done: {roi_updated} updated, {matured} matured"


class Command(BaseCommand):
    help = "Apply 25% daily ROI to all active investments. Mature and credit wallets after 120 days."

    def handle(self, *args, **options):
        now              = timezone.now()
        expiry_threshold = now - timedelta(days=EXPIRY_DAYS)

        roi_updated = 0
        matured     = 0

        expiring_investments = Investment.objects.filter(
            active=True,
            approved=True,
            created_at__lte=expiry_threshold,
        ).select_related("investor")

        for investment in expiring_investments:
            with transaction.atomic():
                daily_gain = investment.amount * (investment.daily_roi / 100)
                investment.current_profit += daily_gain

                payout = investment.amount + investment.current_profit

                investor = Investor.objects.select_for_update().get(pk=investment.investor.pk)
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

        self.stdout.write(
            self.style.SUCCESS(
                f"\n[{now.date()}] Done — {roi_updated} investment(s) updated with 25% daily ROI. "
                f"{matured} investment(s) matured after 120 days and profits credited to wallets."
            )
        )