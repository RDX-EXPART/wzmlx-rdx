from asyncio import gather, iscoroutinefunction
from html import escape
from pyrogram.enums import ButtonStyle
from re import findall
from time import time

from psutil import cpu_percent, disk_usage, virtual_memory

from ... import (
    DOWNLOAD_DIR,
    bot_cache,
    bot_start_time,
    status_dict,
    task_dict,
    task_dict_lock,
)
from ...core.config_manager import Config
from ..telegram_helper.button_build import ButtonMaker

SIZE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]


class MirrorStatus:
    STATUS_UPLOAD = "Upload"
    STATUS_DOWNLOAD = "Download"
    STATUS_CLONE = "Clone"
    STATUS_QUEUEDL = "QueueDl"
    STATUS_QUEUEUP = "QueueUp"
    STATUS_PAUSED = "Pause"
    STATUS_ARCHIVE = "Archive"
    STATUS_EXTRACT = "Extract"
    STATUS_SPLIT = "Split"
    STATUS_CHECK = "CheckUp"
    STATUS_SEED = "Seed"
    STATUS_SAMVID = "SamVid"
    STATUS_CONVERT = "Convert"
    STATUS_FFMPEG = "FFmpeg"
    STATUS_YT = "YouTube"
    STATUS_METADATA = "Metadata"


class EngineStatus:
    def __init__(self):
        ver = bot_cache.get("eng_versions", {})
        self.STATUS_ARIA2 = f"Aria2 v{ver.get('aria2', 'N/A')}"
        self.STATUS_AIOHTTP = f"AioHttp v{ver.get('aiohttp', 'N/A')}"
        self.STATUS_GDAPI = f"Google-API v{ver.get('gapi', 'N/A')}"
        self.STATUS_QBIT = f"qBit v{ver.get('qBittorrent', 'N/A')}"
        self.STATUS_TGRAM = f"Pyro v{ver.get('kurigram', 'N/A')}"
        self.STATUS_MEGA = f"MegaSDK v{ver.get('mega', 'N/A')}"
        self.STATUS_YTDLP = f"yt-dlp v{ver.get('yt-dlp', 'N/A')}"
        self.STATUS_FFMPEG = f"ffmpeg v{ver.get('ffmpeg', 'N/A')}"
        self.STATUS_7Z = f"7z v{ver.get('7z', 'N/A')}"
        self.STATUS_RCLONE = f"RClone v{ver.get('rclone', 'N/A')}"
        self.STATUS_SABNZBD = f"SABnzbd+ v{ver.get('SABnzbd+', 'N/A')}"
        self.STATUS_QUEUE = "QSystem v2"
        self.STATUS_JD = "JDownloader v2"
        self.STATUS_YT = "Youtube-Api"
        self.STATUS_METADATA = "Metadata"
        self.STATUS_UPHOSTER = "Uphoster"


STATUSES = {
    "ALL": "All",
    "DL": MirrorStatus.STATUS_DOWNLOAD,
    "UP": MirrorStatus.STATUS_UPLOAD,
    "QD": MirrorStatus.STATUS_QUEUEDL,
    "QU": MirrorStatus.STATUS_QUEUEUP,
    "AR": MirrorStatus.STATUS_ARCHIVE,
    "EX": MirrorStatus.STATUS_EXTRACT,
    "SD": MirrorStatus.STATUS_SEED,
    "CL": MirrorStatus.STATUS_CLONE,
    "CM": MirrorStatus.STATUS_CONVERT,
    "SP": MirrorStatus.STATUS_SPLIT,
    "SV": MirrorStatus.STATUS_SAMVID,
    "FF": MirrorStatus.STATUS_FFMPEG,
    "PA": MirrorStatus.STATUS_PAUSED,
    "CK": MirrorStatus.STATUS_CHECK,
}


async def get_task_by_gid(gid: str):
    async with task_dict_lock:
        for tk in task_dict.values():
            if hasattr(tk, "seeding"):
                await tk.update()
            if tk.gid() == gid or tk.gid().startswith(gid):
                return tk
        return None


async def get_specific_tasks(status, user_id):
    if status == "All":
        if user_id:
            return [tk for tk in task_dict.values() if tk.listener.user_id == user_id]
        else:
            return list(task_dict.values())
    tasks_to_check = (
        [tk for tk in task_dict.values() if tk.listener.user_id == user_id]
        if user_id
        else list(task_dict.values())
    )
    coro_tasks = []
    coro_tasks.extend(tk for tk in tasks_to_check if iscoroutinefunction(tk.status))
    coro_statuses = await gather(*[tk.status() for tk in coro_tasks])
    result = []
    coro_index = 0
    for tk in tasks_to_check:
        if tk in coro_tasks:
            st = coro_statuses[coro_index]
            coro_index += 1
        else:
            st = tk.status()
        if (st == status) or (
            status == MirrorStatus.STATUS_DOWNLOAD and st not in STATUSES.values()
        ):
            result.append(tk)
    return result


async def get_all_tasks(req_status: str, user_id):
    async with task_dict_lock:
        return await get_specific_tasks(req_status, user_id)


def get_raw_file_size(size):
    num, unit = size.split()
    return int(float(num) * (1024 ** SIZE_UNITS.index(unit)))


def get_readable_file_size(size_in_bytes):
    if not size_in_bytes:
        return "0B"

    index = 0
    while size_in_bytes >= 1024 and index < len(SIZE_UNITS) - 1:
        size_in_bytes /= 1024
        index += 1

    return f"{size_in_bytes:.2f}{SIZE_UNITS[index]}"


def get_readable_time(seconds: int):
    periods = [("d", 86400), ("h", 3600), ("m", 60), ("s", 1)]
    result = ""
    for period_name, period_seconds in periods:
        if seconds >= period_seconds:
            period_value, seconds = divmod(seconds, period_seconds)
            result += f"{int(period_value)}{period_name}"
    return result


def get_raw_time(time_str: str) -> int:
    time_units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    return sum(
        int(value) * time_units[unit]
        for value, unit in findall(r"(\d+)([dhms])", time_str)
    )


def time_to_seconds(time_duration):
    try:
        parts = time_duration.split(":")
        if len(parts) == 3:
            hours, minutes, seconds = map(float, parts)
        elif len(parts) == 2:
            hours = 0
            minutes, seconds = map(float, parts)
        elif len(parts) == 1:
            hours = 0
            minutes = 0
            seconds = float(parts[0])
        else:
            return 0
        return hours * 3600 + minutes * 60 + seconds
    except Exception:
        return 0


def speed_string_to_bytes(size_text: str):
    size = 0
    size_text = size_text.lower()
    if "k" in size_text:
        size += float(size_text.split("k")[0]) * 1024
    elif "m" in size_text:
        size += float(size_text.split("m")[0]) * 1048576
    elif "g" in size_text:
        size += float(size_text.split("g")[0]) * 1073741824
    elif "t" in size_text:
        size += float(size_text.split("t")[0]) * 1099511627776
    elif "b" in size_text:
        size += float(size_text.split("b")[0])
    return size


def get_progress_bar_string(pct):
    pct = float(str(pct).strip("%"))
    p = min(max(pct, 0), 100)
    cFull = int(p * 13 / 100)
    if cFull == 0 and p > 0:
        cFull = 1
    p_str = "●" * cFull + "○" * (13 - cFull)
    return f"[{p_str}]"


async def get_readable_message(sid, is_user, page_no=1, status="All", page_step=1):
    msg = ""
    button = None

    tasks = await get_specific_tasks(status, sid if is_user else None)

    STATUS_LIMIT = Config.STATUS_LIMIT
    tasks_no = len(tasks)
    pages = (max(tasks_no, 1) + STATUS_LIMIT - 1) // STATUS_LIMIT
    if page_no > pages:
        page_no = (page_no - 1) % pages + 1
        status_dict[sid]["page_no"] = page_no
    elif page_no < 1:
        page_no = pages - (abs(page_no) % pages)
        status_dict[sid]["page_no"] = page_no
    start_position = (page_no - 1) * STATUS_LIMIT

    from ..telegram_helper.bot_commands import BotCommands

    # Calculate total DL / UL speed across ALL tasks
    total_dl_speed = 0
    total_up_speed = 0
    for task in tasks:
        try:
            if iscoroutinefunction(task.status):
                _ts = await task.status()
            else:
                _ts = task.status()
            _spd = speed_string_to_bytes(str(task.speed()))
            if _ts == MirrorStatus.STATUS_DOWNLOAD:
                total_dl_speed += _spd
            elif _ts in [
                MirrorStatus.STATUS_UPLOAD,
                MirrorStatus.STATUS_SEED,
                MirrorStatus.STATUS_QUEUEUP,
            ]:
                total_up_speed += _spd
        except Exception:
            pass

    for index, task in enumerate(
        tasks[start_position : STATUS_LIMIT + start_position], start=1
    ):
        if status != "All":
            tstatus = status
        elif iscoroutinefunction(task.status):
            tstatus = await task.status()
        else:
            tstatus = task.status()

        # --- Build per-task block (wrapped in blockquote for full-width rendering) ---
        t = f"<b>{index + start_position}.</b> <b><i>{escape(f'{task.name()}')}</i></b>"
        if task.listener.subname:
            t += f"\n┊ <b>Sub Name</b> » <i>{task.listener.subname}</i>"
        elapsed = time() - task.listener.message.date.timestamp()

        t += (
            f"\n\n╭ <b>Task By</b> "
            f"{task.listener.message.from_user.mention(style='html')} "
            f"( #ID{task.listener.message.from_user.id} )"
        )

        if (
            tstatus not in [MirrorStatus.STATUS_SEED, MirrorStatus.STATUS_QUEUEUP]
            and task.listener.progress
        ):
            progress = task.progress()
            t += f"\n┊ {get_progress_bar_string(progress)} <i>{progress}</i>"
            if task.listener.subname:
                subsize = f" / {get_readable_file_size(task.listener.subsize)}"
                ac = len(task.listener.files_to_proceed)
                count = f"( {task.listener.proceed_count} / {ac or '?'} )"
            else:
                subsize = ""
                count = ""
            # Combine Done + Total on one line (longer line → wider bubble)
            t += (
                f"\n┊ <b>Done</b> » "
                f"<i>{task.processed_bytes()}{subsize}</i> of <i>{task.size()}</i>"
            )
            if count:
                t += f"\n┊ <b>Count</b> » <b>{count}</b>"
            t += (
                f"\n┊ <b>Status</b> » "
                f"<i><a href='https://t.me/rdxmovie_hd'><b>{tstatus}</b></a></i>"
            )
            t += f"\n┊ <b>Speed</b> » <i>{task.speed()}</i>"
            # Combine ETA + Elapsed on one line
            t += (
                f"\n┊ <b>ETA</b> » <i>{task.eta()}</i>"
                f" | <b>Elapsed</b> » <i>{get_readable_time(elapsed)}</i>"
            )
            if tstatus == MirrorStatus.STATUS_DOWNLOAD and (
                task.listener.is_torrent or task.listener.is_qbit
            ):
                try:
                    t += (
                        f"\n┊ <b>Seeders</b> » "
                        f"<i><a href='https://t.me/rdxmovie_hd'>{task.seeders_num()}</a></i>"
                        f" | <b>Leechers</b> » "
                        f"<i><a href='https://t.me/rdxmovie_hd'>{task.leechers_num()}</a></i>"
                    )
                except Exception:
                    pass
        elif tstatus == MirrorStatus.STATUS_SEED:
            # Combine Size + Uploaded on one line
            t += (
                f"\n┊ <b>Size</b> » <i>{task.size()}</i>"
                f" | <b>Uploaded</b> » <i>{task.uploaded_bytes()}</i>"
            )
            t += (
                f"\n┊ <b>Status</b> » "
                f"<i><a href='https://t.me/rdxmovie_hd'><b>{tstatus}</b></a></i>"
            )
            t += f"\n┊ <b>Speed</b> » <i>{task.seed_speed()}</i>"
            t += f"\n┊ <b>Ratio</b> » <i>{task.ratio()}</i>"
            t += (
                f"\n┊ <b>Seed Time</b> » <i>{task.seeding_time()}</i>"
                f" | <b>Elapsed</b> » <i>{get_readable_time(elapsed)}</i>"
            )
        else:
            # FIX: was "\┊" (backslash + ┊, no newline) — corrected to "\n┊"
            t += f"\n┊ <b>Size</b> » <i>{task.size()}</i>"

        t += f"\n┊ <b>Engine</b> » <i>{task.engine}</i>"
        t += f"\n┊ <b>In Mode</b> » <i>{task.listener.mode[0]}</i>"
        t += f"\n┊ <b>Out Mode</b> » <i>{task.listener.mode[1]}</i>"
        # FIX: removed <code> tag — <code> causes narrow monospace bubble in Telegram.
        # Using plain bold text keeps the line at full width.
        t += f"\n╰ <b>Stop</b> » /{BotCommands.CancelTaskCommand[1]}_{task.gid()[:8]}"

        # Wrap each task block in <blockquote> — forces Telegram to render full-width
        msg += f"<blockquote>{t}</blockquote>\n\n"

    if len(msg) == 0:
        if status == "All":
            return None, None
        else:
            msg = f"No Active {status} Tasks!\n\n"

    # --- System Statistics block ---
    du = disk_usage(DOWNLOAD_DIR)
    stats = "<b>⧉ 𝑺𝒚𝒔𝒕𝒆𝒎 𝑺𝒕𝒂𝒕𝒊𝒔𝒕𝒊𝒄𝒔 ⪼</b>\n"
    stats += (
        f"╭ <b>DL</b> » {get_readable_file_size(total_dl_speed)}/s"
        f" | <b>UL</b> » {get_readable_file_size(total_up_speed)}/s\n"
    )
    stats += (
        f"┊ <b>CPU</b> » {cpu_percent()}%"
        f" | <b>FREE</b> » {get_readable_file_size(du.free)}"
        f" [{round(100 - du.percent, 1)}%]\n"
    )
    stats += (
        f"╰ <b>RAM</b> » {virtual_memory().percent}%"
        f" | <b>UP</b> » {get_readable_time(time() - bot_start_time)}"
    )
    if len(tasks) > STATUS_LIMIT:
        stats += (
            f"\n\n<b>Page:</b> {page_no}/{pages}"
            f" | <b>Tasks:</b> {tasks_no}"
            f" | <b>Step:</b> {page_step}"
        )

    msg += f"<blockquote>{stats}</blockquote>"

    buttons = ButtonMaker()
    if not is_user:
        buttons.data_button(
            "📜",
            f"status {sid} ov",
            position="header",
            style=ButtonStyle.PRIMARY,
        )
    if len(tasks) > STATUS_LIMIT:
        buttons.data_button("<<", f"status {sid} pre", position="header")
        buttons.data_button(">>", f"status {sid} nex", position="header")
        if tasks_no > 30:
            for i in [1, 2, 4, 6, 8, 10, 15]:
                buttons.data_button(i, f"status {sid} ps {i}", position="footer")
    if status != "All" or tasks_no > 20:
        for label, status_value in list(STATUSES.items()):
            if status_value != status:
                buttons.data_button(label, f"status {sid} st {status_value}")
    buttons.data_button(
        "♻️", f"status {sid} ref", position="header", style=ButtonStyle.PRIMARY
    )
    button = buttons.build_menu(8)
    return msg, button
