import logging
import os

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"

    def ready(self):
        # Skip during manage.py migrate, makemigrations, collectstatic, etc.
        # Only start the scheduler when the actual server is running.
        import sys
        skip_commands = {
            "migrate", "makemigrations", "collectstatic",
            "check", "shell", "dbshell", "createsuperuser",
            "apply_daily_roi",  # skip if running the management command manually
        }
        if len(sys.argv) > 1 and sys.argv[1] in skip_commands:
            return

        # In dev, Django reloader calls ready() twice.
        # RUN_MAIN=true means we're in the reloader child — skip the parent.
        if os.environ.get("RUN_MAIN") == "true":
            return

        self._start_scheduler()

    @staticmethod
    def _start_scheduler():
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
            from django_apscheduler.jobstores import DjangoJobStore

            scheduler = BackgroundScheduler(timezone="UTC")
            scheduler.add_jobstore(DjangoJobStore(), "default")

            scheduler.add_job(
                _run_daily_roi,
                trigger=CronTrigger(hour=0, minute=0),   # midnight UTC
                id="daily_roi",
                replace_existing=True,
                misfire_grace_time=3600,
            )

            scheduler.start()
            logger.info("APScheduler started — daily ROI job active.")

        except Exception as e:
            logger.error(f"APScheduler failed to start: {e}")


def _run_daily_roi():
    from api.tasks import apply_daily_roi
    apply_daily_roi()