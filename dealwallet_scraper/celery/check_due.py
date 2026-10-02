import shelve
import datetime
import zoneinfo

tz = zoneinfo.ZoneInfo('Asia/Kolkata')
now = datetime.datetime.now(tz)
print(f"Current Time: {now.strftime('%Y-%m-%d %I:%M:%S %p')} IST\n")

db = shelve.open('celerybeat-schedule')
entries = db.get('entries', {})

for task_name, entry in entries.items():
    print(f"Task Name : {task_name}")
    print(f"Last Run  : {entry.last_run_at}")
    rem_time, is_due = entry.is_due()
    print(f"Is Due Now: {is_due}")
    print(f"Remaining : {rem_time} seconds (~{round(rem_time/60, 1)} minutes)")
    print("-" * 50)

db.close()
