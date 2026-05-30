import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import Investment, Investor

logger = logging.getLogger(__name__)

EXPIRY_DAYS = 14


def apply_daily_roi():
    """
    Runs once per day via APScheduler (midnight UTC).

    ROI Policy:
      - Flat 10% ROI calculated ONCE after 14 days (not daily).
      - At maturity: principal + 10% profit credited to investor wallet.
      - Investment is then deactivated automatically.
      - No balance change happens before the 14-day mark.
      - If investor withdraws, all active investments are deactivated
        immediately (handled in WithdrawalViewSet) — no further ROI earned.
    """
    now              = timezone.now()
    expiry_threshold = now - timedelta(days=EXPIRY_DAYS)
    matured          = 0

    expiring = Investment.objects.filter(
        active=True,
        approved=True,
        created_at__lte=expiry_threshold,
    ).select_related("investor")

    for investment in expiring:
        with transaction.atomic():
            investor = Investor.objects.select_for_update().get(
                pk=investment.investor.pk
            )

            # 10% flat profit on original principal
            profit = investment.amount * (investment.daily_roi / 100)
            payout = investment.amount + profit  # principal + profit

            investment.current_profit = profit
            investment.active         = False
            investment.save(update_fields=["current_profit", "active"])

            investor.balance += payout
            investor.save(update_fields=["balance"])

            matured += 1
            logger.info(
                f"[MATURED] {investor.name} | "
                f"Category: {investment.category} | "
                f"Principal: ${float(investment.amount):.2f} | "
                f"Profit: ${float(profit):.2f} | "
                f"Payout: ${float(payout):.2f} | "
                f"New Balance: ${float(investor.balance):.2f}"
            )

    logger.info(
        f"[{now.date()}] ROI job complete — "
        f"{matured} investment(s) matured and credited."
    )
    return f"Done: {matured} matured"