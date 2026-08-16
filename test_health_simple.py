from core.smart_reminders import HealthReminderEngine, ActivityTracker
from datetime import datetime, timedelta

# Test 1: Create HealthReminderEngine
health_engine = HealthReminderEngine()
print("1. HealthReminderEngine created")

# Test 2: Generate movie reminder
reminder = health_engine._generate_movie_health_reminder(150)
assert isinstance(reminder, dict)
assert "message" in reminder
print("2. Movie reminder generated")

# Test 3: Generate work reminder
work_reminder = health_engine._generate_work_break_reminder(90)
assert isinstance(work_reminder, dict)
assert "message" in work_reminder
print("3. Work reminder generated")

# Test 4: ActivityTracker
tracker = ActivityTracker()
tracker.start_activity("movie")
print("4. Activity started")

# Test 5: Simulate long movie watching
tracker.activities["current_session"]["start_time"] = (datetime.now() - timedelta(hours=2.5)).isoformat()
tracker._save_activities()
duration = tracker.get_current_duration()
assert duration > 120
print(f"5. Duration: {duration} minutes")

# Test 6: Check health needs
health_engine2 = HealthReminderEngine()
health_engine2.activity_tracker = tracker
context = {"mode": "movie", "emotion": "neutral"}
reminder_text = health_engine2.check_health_needs(context)
assert reminder_text is not None
assert isinstance(reminder_text, str)
print("6. Health reminder received")

print("\nAll tests passed!")
