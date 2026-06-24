from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler

from .service import WorldCupService


def create_scheduler(service: WorldCupService, date_provider) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    def sync_today() -> None:
        service.sync_date(date_provider())

    scheduler.add_job(sync_today, "interval", minutes=15, id="matchday-sync", replace_existing=True)
    return scheduler
