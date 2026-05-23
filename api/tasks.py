import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import Investment, Investor

logger = logging.getLogger(__name__)


def apply_daily_roi():
    now              = timezone.now()
    expiry_threshold = now - timedelta(days=30)
    roi_updated      = 0
    expired          = 0

    # Step 1 — expire investments older than 30 days
    expiring = Investment.objects.filter(
        active=True,
        created_at__lte=expiry_threshold
    )

    for investment in expiring:
        with transaction.atomic():
            investment.active = False
            investment.save(update_fields=["active"])
            expired += 1
            logger.info(f"Expired investment #{investment.id} for {investment.investor.name}")

    # Step 2 — apply daily ROI to all still-active investments and credit wallet
    for investment in Investment.objects.filter(active=True).select_related("investor"):
        with transaction.atomic():
            daily_gain                = investment.amount * (investment.daily_roi / 100)
            investment.current_profit += daily_gain
            investment.save(update_fields=["current_profit"])

            investor          = Investor.objects.select_for_update().get(pk=investment.investor.pk)
            investor.balance += daily_gain
            investor.save(update_fields=["balance"])

            roi_updated += 1
            logger.info(
                f"ROI applied: {investor.name} | "
                f"+${float(daily_gain):.2f} | "
                f"wallet now ${float(investor.balance):.2f}"
            )

    logger.info(f"[{now.date()}] {roi_updated} updated, {expired} expired")
    return f"Done: {roi_updated} updated, {expired} expired"