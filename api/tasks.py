import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import Investment, Investor

logger = logging.getLogger(__name__)

EXPIRY_DAYS = 14


def apply_daily_roi():
    """
    Called once per day by your scheduler (django-apscheduler or celery beat).
    - Expires investments older than 14 days
    - Applies flat 10% daily ROI to all still-active investments
    - Credits investor wallet balance
    - ROI stops when investment expires or is no longer active
    """
    now              = timezone.now()
    expiry_threshold = now - timedelta(days=EXPIRY_DAYS)
    roi_updated      = 0
    expired          = 0

    # Step 1 — expire investments older than 14 days
    expiring = Investment.objects.filter(
        active=True,
        created_at__lte=expiry_threshold
    )
    for investment in expiring:
        with transaction.atomic():
            investment.active = False
            investment.save(update_fields=["active"])
            expired += 1
            logger.info(
                f"Expired investment #{investment.id} for {investment.investor.name}"
            )

    # Step 2 — apply daily ROI only to still-active investments
    for investment in Investment.objects.filter(active=True).select_related("investor"):
        with transaction.atomic():
            daily_gain                 = investment.amount * (investment.daily_roi / 100)
            investment.current_profit += daily_gain
            investment.save(update_fields=["current_profit"])

            investor          = Investor.objects.select_for_update().get(pk=investment.investor.pk)
            investor.balance += daily_gain
            investor.save(update_fields=["balance"])

            roi_updated += 1
            logger.info(
                f"ROI applied: {investor.name} | "
                f"Category: {investment.category} | "
                f"+${float(daily_gain):.2f} | "
                f"wallet now ${float(investor.balance):.2f}"
            )

    logger.info(f"[{now.date()}] {roi_updated} updated, {expired} expired")
    return f"Done: {roi_updated} updated, {expired} expired"