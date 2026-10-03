"""Напоминание доставляется один раз, даже если бот запущен дважды на одной базе."""
import asyncio

from telegram_bot import reminders as R


async def test_only_one_instance_claims(mem):
    r = await mem.add_reminder(7, "выпить воды", "2026-01-01T00:00:00")
    assert await mem.claim_reminder(r["id"]) is True
    assert await mem.claim_reminder(r["id"]) is False          # второй экземпляр — мимо
    await mem.release_reminder(r["id"])                        # не доставилось — снова в очереди
    assert [x["id"] for x in await mem.get_due_reminders("2027-01-01T00:00:00")] == [r["id"]]


async def test_two_loops_send_once(mem):
    await mem.add_reminder(7, "выпить воды", "2026-01-01T00:00:00")
    sent = []

    class Bot:
        async def send_message(self, chat_id, text):
            sent.append(text)

    class Log:
        def error(self, *a): pass
        def warning(self, *a): pass

    loops = [asyncio.create_task(R.delivery_loop(Bot(), mem, Log(), every=0.01)) for _ in range(2)]
    await asyncio.sleep(0.3)
    for t in loops:
        t.cancel()
    assert len(sent) == 1
