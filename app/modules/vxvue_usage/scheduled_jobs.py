"""Independent cleanup for new usage records; existing chat retention is untouched."""
from apscheduler.triggers.cron import CronTrigger
from . import telemetry


def prune():
    try:telemetry.store().usage_retention()
    except Exception:telemetry.failure()


def register_scheduled_jobs(scheduler):
    scheduler.add_job(prune,CronTrigger(hour=3,minute=10,timezone='Asia/Seoul'),id='vxvue_usage_retention',replace_existing=True,max_instances=1,coalesce=True)
