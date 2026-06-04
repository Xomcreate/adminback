import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import Investment, Investor

logger = logging.getLogger(__name__)

EXPIRY_DAYS = 120


def apply_daily_roi():
    """
    Runs once per day via APScheduler (midnight UTC).

    ROI Policy:
      - 25% daily ROI applied every day for 120 days.
      - At maturity (day 120): principal + all accumulated profit credited to investor wallet.
      - Investment is then deactivated automatically.
      - No withdrawals are permitted until the 120-day lock period has elapsed.
      - If investor withdraws after 120 days, balance is deducted in WithdrawalViewSet.
    """
    now              = timezone.now()
    expiry_threshold = now - timedelta(days=EXPIRY_DAYS)
    matured          = 0
    roi_updated      = 0

    # ── Step 1: Mature investments that have hit 120 days ─────────────────────
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

            # Apply one final day of ROI before closing
            daily_gain = investment.amount * (investment.daily_roi / 100)
            investment.current_profit += daily_gain

            payout = investment.amount + investment.current_profit  # principal + all profit

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

    # ── Step 2: Apply daily 25% ROI to all still-active investments ───────────
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