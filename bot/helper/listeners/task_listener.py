from asyncio import gather, sleep
from time import time

from aiofiles.os import makedirs, path as aiopath, remove
from aiofiles.os import walk as aiowalk

from... import (
    LOGGER,
    intervals,
    non_queued_dl,
    non_queued_up,
    queue_dict_lock,
    queued_dl,
    queued_up,
    same_directory_lock,
    task_dict,
    task_dict_lock,
)
from...core.config_manager import Config
from...core.torrent_manager import TorrentManager
from...core.torrent_manager import TorrentManager
from..ext_utils.bot_utils import sync_to_async
from..ext_utils.db_handler import database
from..ext_utils.files_utils import clean_download
from..ext_utils.status_utils import get_readable_file_size, get_readable_time
from..ext_utils.task_manager import start_from_queued
from..mirror_leech_utils.status_utils.ffmpeg_status import FfmpegStatus
from..mirror_leech_utils.status_utils.gdrive_status import GoogleDriveStatus
from..mirror_leech_utils.status_utils.queue_status import QueueStatus
from..mirror_leech_utils.status_utils.rclone_status import RcloneStatus
from..mirror_leech_utils.status_utils.telegram_status import TelegramStatus
from..mirror_leech_utils.status_utils.yt_status import YtStatus
from..mirror_leech_utils.upload_utils.telegram_uploader import TelegramUploader
from..mirror_leech_utils.youtube_utils.youtube_upload import YouTubeUpload
from..telegram_helper.button_build import ButtonMaker
from..telegram_helper.message_utils import (
    delete_message,
    delete_status,
    send_message,
    update_status_message,
)

class TaskListener(TaskConfig):
    def __init__(self):
        super().__init__()

    async def clean(self):
        with suppress(Exception):
            if st := intervals["status"]:
                for intvl in list(st.values()):
                    intvl.cancel()
            intervals["status"].clear()
            await gather(TorrentManager.aria2.purgeDownloadResult(), delete_status())

    def clear(self):
        self.subname = ""
        self.subsize = 0
        self.files_to_proceed = []
        self.proceed_count = 0
        self.progress = True

    async def remove_from_same_dir(self):
        async with task_dict_lock:
            if (
                self.folder_name
                and self.same_dir
                and self.mid in self.same_dir[self.folder_name]["tasks"]
            ):
                self.same_dir[self.folder_name]["tasks"].remove(self.mid)
                self.same_dir[self.folder_name]["total"] -= 1

    async def on_download_start(self):
        mode_name = "Leech" if self.is_leech else "Mirror"
        if self.bot_pm and self.is_super_chat:
            self.pm_msg = await send_message(
                self.user_id,
                f"""➲ <b><u>Task Started :</u></b>
┃
┖ <b>Link:</b> <a href='{self.source_url}'>Click Here</a>
""",
            )
        if Config.LINKS_LOG_ID:
            await send_message(
                Config.LINKS_LOG_ID,
                f"""➲ <b><u>{mode_name} Started:</u></b>
 ┃
 ┠ <b>User :</b> {self.tag} ( #ID{self.user_id} )
 ┠ <b>Message Link :</b> <a href='{self.message.link}'>Click Here</a>
 ┗ <b>Link:</b> <a href='{self.source_url}'>Click Here</a>
 """,
            )
        if (
            self.is_super_chat
            and Config.INCOMPLETE_TASK_NOTIFIER
            and Config.DATABASE_URL
        ):
            await database.add_incomplete_task(
                self.message.chat.id, self.message.link, self.tag
            )

    async def on_download_complete(self):
        await sleep(2)
        if self.is_cancelled:
            return
        multi_links = False
        if (
            self.folder_name
            and self.same_dir
            and self.mid in self.same_dir[self.folder_name]["tasks"]
        ):
            async with same_directory_lock:
                while True:
                    async with task_dict_lock:
                        if self.mid not in self.same_dir[self.folder_name]["tasks"]:
                            return
                        if (
                            self.same_dir[self.folder_name]["total"] <= 1
                            or len(self.same_dir[self.folder_name]["tasks"]) > 1
                        ):
                            if self.same_dir[self.folder_name]["total"] > 1:
                                self.same_dir[self.folder_name]["tasks"].remove(
                                    self.mid
                                )
                                self.same_dir[self.folder_name]["total"] -= 1
                                spath = f"{self.dir}/!qB"
                                if await aiopath.exists(spath):
                                    async with queue_dict_lock:
                                        if self.mid in non_queued_dl:
                                            non_queued_dl.remove(self.mid)
                                    return
                                for fd_name in self.same_dir:
                                    if fd_name!= self.folder_name:
                                        spath = f"{self.dir}/{fd_name}/!qB"
                                        if await aiopath.exists(spath):
                                            async with queue_dict_lock:
                                                if self.mid in non_queued_dl:
                                                    non_queued_dl.remove(self.mid)
                                            return
                            break
                    await sleep(2)
            async with same_directory_lock:
                for fd_name in self.same_dir:
                    if self.folder_name!= fd_name:
                        self.same_dir[self.folder_name]["tasks"].update(
                            self.same_dir[fd_name]["tasks"]
                        )
                    if self.same_dir[fd_name]["total"] > 1:
                        multi_links = True
                if multi_links:
                    self.same_dir[self.folder_name]["total"] = len(
                        self.same_dir[self.folder_name]["tasks"]
                    )

        # ========== চেইন মোড: -e -vt এর জন্য ==========
        if hasattr(self, 'is_vt_chain') and self.is_vt_chain:
            from bot.modules.merge import process_video_video
            import os

            videos = []
            for root, _, files in os.walk(self.dir):
                for file in files:
                    if file.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm', '.m4v')):
                        videos.append(os.path.join(root, file))

            if len(videos) >= 2:
                await send_message(self.message, f"আনজিপ শেষ ✅ {len(videos)} টা ভিডিও পেয়েছি। এখন মার্জ হচ্ছে...")
                # প্রথম 2টা ভিডিও মার্জ করবে
                await process_video_video(self.client, self.message, {'chat_id': self.message.chat.id}, videos[:2])
                return # মার্জে পাঠিয়ে এখানেই শেষ
            else:
                await send_message(self.message, "আনজিপের পর 2টা ভিডিও পাওয়া যায়নি, নরমাল আপলোড হচ্ছে")
        # ========== চেইন মোড শেষ ==========

        if self.is_leech:
            LOGGER.info(f"Leech Name: {self.name}")
            LeechUploader = TelegramUploader(self, self.message)
            async with task_dict_lock:
                task_dict[self.mid] = TelegramStatus(self, LeechUploader, self.mid, "up")
            await gather(
                update_status_message(self.message.chat.id),
                LeechUploader.upload(),
            )
            del LeechUploader
        else:
            # Mirror/Rclone/YTDL কোড অপরিবর্তিত
            pass

    async def on_upload_complete(
        self, link, files, folders, mime_type, rclone_path="", dir_id=""
    ):
        if (
            self.is_super_chat
            and Config.INCOMPLETE_TASK_NOTIFIER
            and Config.DATABASE_URL
        ):
            await database.rm_complete_task(self.message.link)
        msg = (
            f"<b><i>{escape(self.name)}</i></b>\n│"
            f"\n┟ <b>Task Size</b> → {get_readable_file_size(self.size)}"
            f"\n┠ <b>Time Taken</b> → {get_readable_time(time() - self.message.date.timestamp())}"
            f"\n┠ <b>In Mode</b> → {self.mode[0]}"
            f"\n┠ <b>Out Mode</b> → {self.mode[1]}"
        )
        LOGGER.info(f"Task Done: {self.name}")
        #... বাকি on_upload_complete কোড অপরিবর্তিত

    async def on_download_error(self, error, button=None):
        async with task_dict_lock:
            if self.mid in task_dict:
                del task_dict[self.mid]
            count = len(task_dict)
        await send_message(self.message, f"{self.tag} {escape(str(error))}")
        if count == 0:
            await self.clean()
        else:
            await update_status_message(self.message.chat.id)

        if (
            self.is_super_chat
            and Config.INCOMPLETE_TASK_NOTIFIER
            and Config.DATABASE_URL
        ):
            await database.rm_complete_task(self.message.link)

        async with queue_dict_lock:
            if self.mid in queued_dl:
                queued_dl[self.mid].set()
                del queued_dl[self.mid]
            if self.mid in queued_up:
                queued_up[self.mid].set()
                del queued_up[self.mid]
            if self.mid in non_queued_dl:
                non_queued_dl.remove(self.mid)
            if self.mid in non_queued_up:
                non_queued_up.remove(self.mid)

        await start_from_queued()
        await sleep(3)
        await clean_download(self.dir)
        if self.up_dir:
            await clean_download(self.up_dir)
        if self.thumb and await aiopath.exists(self.thumb):
            await remove(self.thumb)
