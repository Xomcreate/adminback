from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'

    def ready(self):
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        scheduler = BackgroundScheduler()

        scheduler.add_job(
            self._run_daily_roi,
            trigger=CronTrigger(hour=0, minute=0),  # runs every day at midnight
            id='daily_roi',
            replace_existing=True,
        )

        scheduler.start()

    @staticmethod
    def _run_daily_roi():
        from api.tasks import apply_daily_roi
        apply_daily_roi()