from __future__ import annotations

from asyncio import CancelledError, Task, create_task, sleep
from html import escape
from time import time
from typing import Any

from pyrogram.enums import ChatType

from .. import LOGGER
from ..helper.telegram_helper.message_utils import send_message
from . import game_boss as game_boss_module
from .game_common import (
    entertainment_enabled,
    ensure_message_user,
    format_number,
    game_collection,
)

AUTO_DUMMY_DEFAULT_INTERVAL = 3
AUTO_BOSS_DEFAULT_INTERVAL = 3
AUTO_MIN_INTERVAL = 2
AUTO_MAX_INTERVAL = 300

TaskKey = tuple[int, int]
AUTO_DUMMY_TASKS: dict[TaskKey, dict[str, Any]] = {}
AUTO_BOSS_TASKS: dict[TaskKey, dict[str, Any]] = {}


def _unwrap_handler(handler):
    current = handler
    while hasattr(current, "__wrapped__"):
        current = current.__wrapped__
    return current


_TRAINING_DUMMY_ONCE = _unwrap_handler(game_boss_module.training_dummy)
_SUMMON_BOSS_ONCE = _unwrap_handler(game_boss_module.summon_boss)
_ATTACK_BOSS_ONCE = _unwrap_handler(game_boss_module.attack_boss)


class _SilentMessage:
    """Message proxy that preserves command context but captures replies."""

    def __init__(self, source, text: str):
        self._source = source
        self.text = text
        self.replies: list[str] = []

    def __getattr__(self, name: str):
        return getattr(self._source, name)

    async def reply(self, *args, **kwargs):
        text = kwargs.get("text")
        if text is None and args:
            text = args[0]
        if text is not None:
            self.replies.append(str(text))
        return self

    def output(self) -> str:
        return "\n".join(self.replies)


def _command_name(message) -> str:
    first = (message.text or "").split(maxsplit=1)[0]
    return first.lstrip("/").split("@", 1)[0].lower()


def _task_key(message) -> TaskKey:
    return int(message.from_user.id), int(message.chat.id)


def _parse_interval(raw: str | None, default: int) -> int | None:
    if raw is None:
        return default
    try:
        interval = int(raw)
    except (TypeError, ValueError):
        return None
    if not AUTO_MIN_INTERVAL <= interval <= AUTO_MAX_INTERVAL:
        return None
    return interval


def _stats(document: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(document, dict):
        return {}
    value = document.get("stats", {})
    return value if isinstance(value, dict) else {}


def _delta(current: dict[str, Any], baseline: dict[str, Any], field: str) -> int:
    return int(current.get(field, 0) or 0) - int(baseline.get(field, 0) or 0)


async def _current_user_document(message):
    collection = game_collection()
    if collection is None or message.from_user is None:
        return None
    return await ensure_message_user(collection, message)


async def _dummy_summary(message, state: dict[str, Any]) -> str:
    document = await _current_user_document(message)
    current = _stats(document)
    baseline = state["baseline"]
    elapsed = max(0, int(time() - state["started_at"]))
    return (
        "🎯 <b>Auto bù nhìn</b>\n"
        f"Trạng thái: <b>{'Đang chạy' if state.get('running') else 'Đã dừng'}</b>\n"
        f"Chu kỳ: <b>{state['interval']} giây/lần</b>\n"
        f"Thời gian: <b>{elapsed // 60} phút {elapsed % 60} giây</b>\n"
        f"Số lượt: <b>{format_number(_delta(current, baseline, 'dummy_hits'))}</b>\n"
        f"Sát thương: <b>{format_number(_delta(current, baseline, 'dummy_damage'))}</b>\n"
        f"XP nhận: <b>{format_number(_delta(current, baseline, 'dummy_xp'))}</b>"
    )


async def _boss_summary(message, state: dict[str, Any]) -> str:
    document = await _current_user_document(message)
    current = _stats(document)
    baseline = state["baseline"]
    elapsed = max(0, int(time() - state["started_at"]))
    selector = state["selector"]
    return (
        "👹 <b>Auto boss</b>\n"
        f"Trạng thái: <b>{'Đang chạy' if state.get('running') else 'Đã dừng'}</b>\n"
        f"Boss gọi: <code>{escape(selector)}</code>\n"
        f"Chu kỳ: <b>{state['interval']} giây/lần</b>\n"
        f"Thời gian: <b>{elapsed // 60} phút {elapsed % 60} giây</b>\n"
        f"Đã gọi: <b>{format_number(state.get('summons', 0))}</b> boss\n"
        f"Đòn đánh: <b>{format_number(_delta(current, baseline, 'boss_hits'))}</b>\n"
        f"Sát thương: <b>{format_number(_delta(current, baseline, 'boss_damage'))}</b>\n"
        f"Boss kết liễu: <b>{format_number(_delta(current, baseline, 'boss_kills'))}</b>\n"
        f"Xu thưởng: <b>{format_number(_delta(current, baseline, 'boss_rewards'))}</b>"
    )


async def _auto_dummy_loop(key: TaskKey, state: dict[str, Any]) -> None:
    message = state["message"]
    paused = False
    try:
        while True:
            if not await entertainment_enabled(message.chat.id):
                if not paused:
                    paused = True
                    await send_message(
                        message,
                        "⏸ Auto bù nhìn tạm dừng vì khu vực giải trí đang đóng.",
                    )
                await sleep(10)
                continue

            if paused:
                paused = False
                await send_message(
                    message,
                    "▶️ Khu vực giải trí đã mở; auto bù nhìn tiếp tục.",
                )

            proxy = _SilentMessage(message, "/bunhin")
            await _TRAINING_DUMMY_ONCE(None, proxy)
            state["runs"] += 1
            output = proxy.output()
            if "MongoDB chưa sẵn sàng" in output or "cần MongoDB" in output:
                await send_message(message, "❌ Auto bù nhìn dừng: MongoDB chưa sẵn sàng.")
                break
            await sleep(state["interval"])
    except CancelledError:
        raise
    except Exception as error:
        LOGGER.exception("Auto training dummy failed")
        await send_message(
            message,
            f"❌ Auto bù nhìn đã dừng do lỗi: <code>{escape(str(error))}</code>",
        )
    finally:
        state["running"] = False
        if AUTO_DUMMY_TASKS.get(key) is state:
            AUTO_DUMMY_TASKS.pop(key, None)


async def _auto_boss_loop(key: TaskKey, state: dict[str, Any]) -> None:
    message = state["message"]
    paused = False
    try:
        while True:
            if not await entertainment_enabled(message.chat.id):
                if not paused:
                    paused = True
                    await send_message(
                        message,
                        "⏸ Auto boss tạm dừng vì khu vực giải trí đang đóng.",
                    )
                await sleep(10)
                continue

            if paused:
                paused = False
                await send_message(
                    message,
                    "▶️ Khu vực giải trí đã mở; auto boss tiếp tục.",
                )

            bosses = game_boss_module.boss_collection()
            if bosses is None:
                await send_message(message, "❌ Auto boss dừng: MongoDB chưa sẵn sàng.")
                break

            boss = await game_boss_module._expire_boss_if_needed(
                bosses,
                message.chat.id,
            )
            if not boss or boss.get("status") != "active":
                summon_text = "/goiboss"
                if state["selector"] != "random":
                    summon_text += f" {state['selector']}"
                proxy = _SilentMessage(message, summon_text)
                await _SUMMON_BOSS_ONCE(None, proxy)
                output = proxy.output()

                if "xuất hiện" in output:
                    state["summons"] += 1
                    await send_message(message, output)
                elif "Cần" in output and "gọi boss" in output:
                    await send_message(
                        message,
                        output + "\n\n⛔ Auto boss đã tự dừng vì không đủ xu.",
                    )
                    break
                elif "Không tìm thấy boss" in output or "chỉ có thể được gọi trong nhóm" in output:
                    await send_message(message, output + "\n\n⛔ Auto boss đã dừng.")
                    break
            else:
                proxy = _SilentMessage(message, "/danhboss")
                await _ATTACK_BOSS_ONCE(None, proxy)
                output = proxy.output()
                state["runs"] += 1

                if "đã bị tiêu diệt" in output:
                    await send_message(message, output)
                elif "đã biến mất" in output:
                    warning_lines = [
                        line
                        for line in output.splitlines()
                        if "đã biến mất" in line or "HP còn" in line
                    ]
                    if warning_lines:
                        await send_message(message, "\n".join(warning_lines))
                elif "đã gục ngã" in output:
                    await send_message(
                        message,
                        "💀 Auto boss đang chờ nhân vật hồi sinh rồi sẽ đánh tiếp.",
                    )
                    await sleep(10)

            await sleep(state["interval"])
    except CancelledError:
        raise
    except Exception as error:
        LOGGER.exception("Auto boss failed")
        await send_message(
            message,
            f"❌ Auto boss đã dừng do lỗi: <code>{escape(str(error))}</code>",
        )
    finally:
        state["running"] = False
        if AUTO_BOSS_TASKS.get(key) is state:
            AUTO_BOSS_TASKS.pop(key, None)


async def _cancel_task(
    registry: dict[TaskKey, dict[str, Any]],
    key: TaskKey,
) -> dict[str, Any] | None:
    state = registry.get(key)
    if state is None:
        return None
    state["running"] = False
    task: Task | None = state.get("task")
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except CancelledError:
            pass
    registry.pop(key, None)
    return state


async def auto_training_dummy(_, message):
    if message.from_user is None:
        return
    parts = (message.text or "").split()
    action = parts[1].lower() if len(parts) > 1 else "status"
    key = _task_key(message)

    if action in {"off", "tat", "tắt", "stop"}:
        state = await _cancel_task(AUTO_DUMMY_TASKS, key)
        if state is None:
            await send_message(message, "ℹ️ Auto bù nhìn hiện không chạy.")
            return
        await send_message(message, await _dummy_summary(message, state))
        return

    if action in {"status", "trangthai", "trạngthái"}:
        state = AUTO_DUMMY_TASKS.get(key)
        if state is None:
            await send_message(
                message,
                "🎯 Auto bù nhìn: <b>Đang tắt</b>.\n"
                "Bật bằng <code>/autobunhin on [2-300 giây]</code>.",
            )
            return
        await send_message(message, await _dummy_summary(message, state))
        return

    if action not in {"on", "bat", "bật", "start"} or len(parts) > 3:
        await send_message(
            message,
            "Cách dùng:\n"
            "<code>/autobunhin on [2-300 giây]</code>\n"
            "<code>/autobunhin off</code>\n"
            "<code>/autobunhin status</code>",
        )
        return

    interval = _parse_interval(parts[2] if len(parts) > 2 else None, AUTO_DUMMY_DEFAULT_INTERVAL)
    if interval is None:
        await send_message(message, "❌ Chu kỳ phải từ 2 đến 300 giây.")
        return
    if not await entertainment_enabled(message.chat.id):
        await send_message(message, "⛔ Khu vực giải trí đang tạm đóng.")
        return
    current = AUTO_DUMMY_TASKS.get(key)
    if current is not None and current.get("running"):
        await send_message(message, await _dummy_summary(message, current))
        return

    document = await _current_user_document(message)
    if document is None:
        await send_message(message, "❌ MongoDB chưa sẵn sàng.")
        return
    state: dict[str, Any] = {
        "message": message,
        "interval": interval,
        "started_at": time(),
        "baseline": dict(_stats(document)),
        "runs": 0,
        "running": True,
        "task": None,
    }
    AUTO_DUMMY_TASKS[key] = state
    state["task"] = create_task(_auto_dummy_loop(key, state))
    await send_message(
        message,
        "✅ Đã bật <b>auto bù nhìn</b>.\n"
        f"Chu kỳ: <b>{interval} giây/lần</b>.\n"
        "Dùng <code>/autobunhin off</code> để dừng. "
        "Tác vụ tự dừng khi bot khởi động lại.",
    )


async def auto_boss(_, message):
    if message.from_user is None:
        return
    parts = (message.text or "").split()
    action = parts[1].lower() if len(parts) > 1 else "status"
    key = _task_key(message)

    if action in {"off", "tat", "tắt", "stop"}:
        state = await _cancel_task(AUTO_BOSS_TASKS, key)
        if state is None:
            await send_message(message, "ℹ️ Auto boss hiện không chạy trong nhóm này.")
            return
        await send_message(message, await _boss_summary(message, state))
        return

    if action in {"status", "trangthai", "trạngthái"}:
        state = AUTO_BOSS_TASKS.get(key)
        if state is None:
            await send_message(
                message,
                "👹 Auto boss: <b>Đang tắt</b>.\n"
                "Bật bằng <code>/autoboss on [random|boss_id] [2-300 giây]</code>.",
            )
            return
        await send_message(message, await _boss_summary(message, state))
        return

    if action not in {"on", "bat", "bật", "start"} or len(parts) > 4:
        await send_message(
            message,
            "Cách dùng:\n"
            "<code>/autoboss on</code> — gọi boss ngẫu nhiên và tự đánh\n"
            "<code>/autoboss on slime_king 3</code> — gọi boss chỉ định\n"
            "<code>/autoboss off</code>\n"
            "<code>/autoboss status</code>",
        )
        return
    if message.chat.type == ChatType.PRIVATE:
        await send_message(message, "❌ Auto boss chỉ dùng trong nhóm.")
        return

    selector = "random"
    interval_raw: str | None = None
    remaining = parts[2:]
    if remaining:
        if remaining[0].isdigit():
            interval_raw = remaining[0]
        else:
            selector = remaining[0].strip().lower()
            if len(remaining) > 1:
                interval_raw = remaining[1]
    interval = _parse_interval(interval_raw, AUTO_BOSS_DEFAULT_INTERVAL)
    if interval is None:
        await send_message(message, "❌ Chu kỳ phải từ 2 đến 300 giây.")
        return

    if selector not in {"random", "rand", "ngau_nhien", "ngaunhien"}:
        template = game_boss_module._find_boss(selector)
        if template is None:
            await send_message(
                message,
                "❌ Boss ID không tồn tại. Dùng <code>/goiboss list</code> để xem.",
            )
            return
        selector = str(template["id"])
    else:
        selector = "random"

    if not await entertainment_enabled(message.chat.id):
        await send_message(message, "⛔ Khu vực giải trí đang tạm đóng.")
        return
    current = AUTO_BOSS_TASKS.get(key)
    if current is not None and current.get("running"):
        await send_message(message, await _boss_summary(message, current))
        return

    document = await _current_user_document(message)
    if document is None:
        await send_message(message, "❌ MongoDB chưa sẵn sàng.")
        return
    state: dict[str, Any] = {
        "message": message,
        "interval": interval,
        "selector": selector,
        "started_at": time(),
        "baseline": dict(_stats(document)),
        "runs": 0,
        "summons": 0,
        "running": True,
        "task": None,
    }
    AUTO_BOSS_TASKS[key] = state
    state["task"] = create_task(_auto_boss_loop(key, state))
    summon_cost = (
        game_boss_module.BOSS_RANDOM_SUMMON_COST
        if selector == "random"
        else game_boss_module.BOSS_TARGETED_SUMMON_COST
    )
    await send_message(
        message,
        "✅ Đã bật <b>auto gọi và đánh boss</b>.\n"
        f"Boss: <code>{escape(selector)}</code> · chu kỳ: <b>{interval} giây</b>.\n"
        f"Mỗi lần gọi tốn <b>{format_number(summon_cost)} xu</b>.\n"
        "Dùng <code>/autoboss off</code> để dừng. "
        "Tác vụ tự dừng khi bot khởi động lại.",
    )


async def training_dummy_dispatch(client, message):
    if _command_name(message).startswith("autobunhin"):
        return await auto_training_dummy(client, message)
    return await game_boss_module.training_dummy(client, message)


async def attack_boss_dispatch(client, message):
    if _command_name(message).startswith("autoboss"):
        return await auto_boss(client, message)
    return await game_boss_module.attack_boss(client, message)
