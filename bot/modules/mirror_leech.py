from asyncio import CancelledError, sleep
from contextlib import suppress
from base64 import b64encode
from html import escape
from re import match as re_match
from urllib.parse import urlparse

from aiofiles.os import path as aiopath
from bot.core.config_manager import Config

from .. import DOWNLOAD_DIR, LOGGER, bot_loop, task_dict_lock
from ..helper.ext_utils.bot_utils import (
    COMMAND_USAGE,
    arg_parser,
    get_content_type,
    sync_to_async,
)
from ..helper.ext_utils.exceptions import DirectDownloadLinkException
from ..helper.ext_utils.links_utils import (
    is_gdrive_id,
    is_gdrive_link,
    is_mega_link,
    is_magnet,
    is_rclone_path,
    is_telegram_link,
    is_url,
)
from ..helper.ext_utils.task_manager import pre_task_check
from ..helper.listeners.task_listener import TaskListener
from ..helper.mirror_leech_utils.download_utils.aria2_download import (
    add_aria2_download,
)
from ..helper.mirror_leech_utils.download_utils.direct_downloader import (
    add_direct_download,
)
from ..helper.mirror_leech_utils.download_utils.direct_link_generator import (
    direct_link_generator,
)
from ..helper.mirror_leech_utils.download_utils.gd_download import add_gd_download
from ..helper.mirror_leech_utils.download_utils.jd_download import add_jd_download
from ..helper.mirror_leech_utils.download_utils.mega_download import add_mega_download
from ..helper.mirror_leech_utils.download_utils.nzb_downloader import add_nzb
from ..helper.mirror_leech_utils.download_utils.qbit_download import add_qb_torrent
from ..helper.mirror_leech_utils.download_utils.rclone_download import (
    add_rclone_download,
)
from ..helper.mirror_leech_utils.download_utils.telegram_download import (
    TelegramDownloadHelper,
)
from ..helper.telegram_helper.message_utils import (
    auto_delete_message,
    delete_links,
    edit_message,
    get_tg_link_message,
    send_message,
)


FAST_BYPASS_HOSTS = (
    "gofile.io",
    "hubcloud.one",
    "hubcloud.foo",
    "hubcloud.ink",
    "hubcloud.art",
    "gdflix.net",
    "gdflix.dev",
    "gdflix.wtf",
    "gdflix.zip",
    "pixeldrain.com",
    "pixeldrain.dev",
)

SOURCE_NAMES = {
    "gdflix": "GDFlix",
    "hubcloud": "HubCloud",
    "gofile": "GoFile",
    "pixeldrain": "PixelDrain",
    "mediafire": "MediaFire",
    "terabox": "TeraBox",
    "streamtape": "StreamTape",
    "filelions": "FileLions",
    "streamwish": "StreamWish",
    "linkbox": "LinkBox",
}


def _get_domain(link):
    try:
        return (urlparse(str(link)).hostname or "").lower()
    except Exception:
        return ""


def _is_fast_bypass_host(link):
    domain = _get_domain(link)
    return any(host in domain for host in FAST_BYPASS_HOSTS)


def _pretty_source(link):
    domain = _get_domain(link)
    if not domain:
        return "Direct Link"
    for key, name in SOURCE_NAMES.items():
        if key in domain:
            return name
    return domain.replace("www.", "").split(".")[0].title()


def _detect_link_sources(original_link, generated_link=None):
    source = _pretty_source(original_link)
    mirror = _pretty_source(generated_link) if generated_link else "Direct Link"
    if mirror == "Direct Link":
        mirror = source
    return source, mirror


def _extract_display_link(generated):
    if isinstance(generated, tuple):
        return str(generated[0])
    if isinstance(generated, str):
        return generated
    if isinstance(generated, dict):
        if generated.get("zip_url"):
            return str(generated["zip_url"])
        contents = generated.get("contents") or []
        if contents and isinstance(contents[0], dict) and contents[0].get("url"):
            return str(contents[0]["url"])
        for key in ("url", "direct_link", "downloadUrl", "download_url"):
            if generated.get(key):
                return str(generated[key])
    return ""


def _direct_link_found_text(source, mirror, display_link, mode_text):
    if len(display_link) > 3300:
        display_link = display_link[:3300] + "..."
    return (
        "✅ <b>Direct link generated!</b>\n\n"
        f"🌐 <b>Source:</b> {escape(source)}\n"
        f"🪞 <b>Mirror Found:</b> {escape(mirror)}\n"
        "<i>Found direct link:</i>\n"
        f"<code>{escape(display_link)}</code>\n\n"
        f"🚀 <b>Starting {escape(mode_text)}...</b>"
    )


async def _animate_bypass_progress(status_msg):
    """Typing-style progress animation using one Telegram message only."""
    frames = (
        "🔄 <b>Processing link</b>\n⚡ Checking bypass engine.",
        "🔄 <b>Processing link.</b>\n⚡ Checking bypass engine..",
        "🔄 <b>Processing link..</b>\n⚡ Checking bypass engine...",
        "🔗 <b>Generating direct link...</b>\n🚀 Finding the fastest mirror.",
    )
    index = 0
    while True:
        with suppress(Exception):
            await edit_message(status_msg, frames[index % len(frames)])
        index += 1
        await sleep(0.8)


class Mirror(TaskListener):
    def __init__(
        self,
        client,
        message,
        is_qbit=False,
        is_leech=False,
        is_jd=False,
        is_nzb=False,
        is_uphoster=False,
        same_dir=None,
        bulk=None,
        multi_tag=None,
        options="",
        **kwargs,
    ):
        if same_dir is None:
            same_dir = {}
        if bulk is None:
            bulk = []
        self.message = message
        self.client = client
        self.multi_tag = multi_tag
        self.options = options
        self.same_dir = same_dir
        self.bulk = bulk
        super().__init__()
        self.is_qbit = is_qbit
        self.is_leech = is_leech
        self.is_jd = is_jd
        self.is_nzb = is_nzb
        self.is_uphoster = is_uphoster

    async def new_event(self):
        text = self.message.text.split("\n")
        input_list = text[0].split(" ")

        check_msg, check_button = await pre_task_check(self.message)
        if check_msg:
            await delete_links(self.message)
            await auto_delete_message(
                await send_message(self.message, check_msg, check_button)
            )
            return

        args = {
            "-doc": False,
            "-med": False,
            "-d": False,
            "-j": False,
            "-s": False,
            "-b": False,
            "-e": False,
            "-z": False,
            "-sv": False,
            "-ss": False,
            "-f": False,
            "-fd": False,
            "-fu": False,
            "-hl": False,
            "-bt": False,
            "-ut": False,
            "-yt": False,
            "-i": 0,
            "-sp": 0,
            "link": "",
            "-n": "",
            "-m": "",
            "-meta": "",
            "-up": "",
            "-rcf": "",
            "-au": "",
            "-ap": "",
            "-h": "",
            "-t": "",
            "-ca": "",
            "-cv": "",
            "-ns": "",
            "-tl": "",
            "-ff": set(),
        }

        arg_parser(input_list[1:], args)

        if Config.DISABLE_BULK and args.get("-b", False):
            await send_message(self.message, "Bulk downloads are currently disabled.")
            return

        if Config.DISABLE_MULTI and int(args.get("-i", 1)) > 1:
            await send_message(
                self.message,
                "Multi-downloads are currently disabled. Please try without the -i flag.",
            )
            return

        if Config.DISABLE_SEED and args.get("-d", False):
            await send_message(
                self.message,
                "Seeding is currently disabled. Please try without the -d flag.",
            )
            return

        if Config.DISABLE_FF_MODE and args.get("-ff"):
            await send_message(self.message, "FFmpeg commands are currently disabled.")
            return

        self.select = args["-s"]
        self.seed = args["-d"]
        self.name = args["-n"]
        self.up_dest = args["-up"]
        self.rc_flags = args["-rcf"]
        self.link = args["link"]
        self.compress = args["-z"]
        self.extract = args["-e"]
        self.join = args["-j"]
        self.thumb = args["-t"]
        self.split_size = args["-sp"]
        self.sample_video = args["-sv"]
        self.screen_shots = args["-ss"]
        self.force_run = args["-f"]
        self.force_download = args["-fd"]
        self.force_upload = args["-fu"]
        self.convert_audio = args["-ca"]
        self.convert_video = args["-cv"]
        self.name_swap = args["-ns"]
        self.hybrid_leech = args["-hl"]
        self.thumbnail_layout = args["-tl"]
        self.as_doc = args["-doc"]
        self.as_med = args["-med"]
        self.folder_name = f"/{args['-m']}".rstrip("/") if len(args["-m"]) > 0 else ""
        self.bot_trans = args["-bt"]
        self.user_trans = args["-ut"]
        self.is_yt = args["-yt"]
        self.metadata_dict = self.default_metadata_dict.copy()
        self.audio_metadata_dict = self.audio_metadata_dict.copy()
        self.video_metadata_dict = self.video_metadata_dict.copy()
        self.subtitle_metadata_dict = self.subtitle_metadata_dict.copy()
        if args["-meta"]:
            meta = self.metadata_processor.parse_string(args["-meta"])
            self.metadata_dict = self.metadata_processor.merge_dicts(
                self.metadata_dict, meta
            )

        headers = args["-h"]
        is_bulk = args["-b"]

        bulk_start = 0
        bulk_end = 0
        ratio = None
        seed_time = None
        reply_to = None
        file_ = None
        session = ""

        try:
            self.multi = int(args["-i"])
        except Exception:
            self.multi = 0

        try:
            if args["-ff"]:
                if isinstance(args["-ff"], set):
                    self.ffmpeg_cmds = args["-ff"]
                else:
                    self.ffmpeg_cmds = eval(args["-ff"])
        except Exception as e:
            self.ffmpeg_cmds = None
            LOGGER.error(e)

        if not isinstance(self.seed, bool):
            dargs = self.seed.split(":")
            ratio = dargs[0] or None
            if len(dargs) == 2:
                seed_time = dargs[1] or None
            self.seed = True

        if not isinstance(is_bulk, bool):
            dargs = is_bulk.split(":")
            bulk_start = dargs[0] or 0
            if len(dargs) == 2:
                bulk_end = dargs[1] or 0
            is_bulk = True

        if not is_bulk:
            if self.multi > 0:
                if self.folder_name:
                    async with task_dict_lock:
                        if self.folder_name in self.same_dir:
                            self.same_dir[self.folder_name]["tasks"].add(self.mid)
                            for fd_name in self.same_dir:
                                if fd_name != self.folder_name:
                                    self.same_dir[fd_name]["total"] -= 1
                        elif self.same_dir:
                            self.same_dir[self.folder_name] = {
                                "total": self.multi,
                                "tasks": {self.mid},
                            }
                            for fd_name in self.same_dir:
                                if fd_name != self.folder_name:
                                    self.same_dir[fd_name]["total"] -= 1
                        else:
                            self.same_dir = {
                                self.folder_name: {
                                    "total": self.multi,
                                    "tasks": {self.mid},
                                }
                            }
                elif self.same_dir:
                    async with task_dict_lock:
                        for fd_name in self.same_dir:
                            self.same_dir[fd_name]["total"] -= 1
        else:
            await self.init_bulk(input_list, bulk_start, bulk_end, Mirror)
            return

        if len(self.bulk) != 0:
            del self.bulk[0]

        await self.run_multi(input_list, Mirror)

        await self.get_tag(text)

        path = f"{DOWNLOAD_DIR}{self.mid}{self.folder_name}"

        if not self.link and (reply_to := self.message.reply_to_message):
            if reply_to.text:
                self.link = reply_to.text.split("\n", 1)[0].strip()
        if is_telegram_link(self.link):
            try:
                reply_to, session = await get_tg_link_message(self.link)
            except Exception as e:
                await send_message(self.message, f"ERROR: {e}")
                await self.remove_from_same_dir()
                await delete_links(self.message)
                return

        if isinstance(reply_to, list):
            self.bulk = reply_to
            b_msg = input_list[:1]
            self.options = " ".join(input_list[1:])
            b_msg.append(f"{self.bulk[0]} -i {len(self.bulk)} {self.options}")
            nextmsg = await send_message(self.message, " ".join(b_msg))
            nextmsg = await self.client.get_messages(
                chat_id=self.message.chat.id, message_ids=nextmsg.id
            )
            if self.message.from_user:
                nextmsg.from_user = self.user
            else:
                nextmsg.sender_chat = self.user
            await Mirror(
                self.client,
                nextmsg,
                self.is_qbit,
                self.is_leech,
                self.is_jd,
                self.is_nzb,
                self.is_uphoster,
                self.same_dir,
                self.bulk,
                self.multi_tag,
                self.options,
            ).new_event()
            return

        if reply_to:
            file_ = (
                reply_to.document
                or reply_to.photo
                or reply_to.video
                or reply_to.audio
                or reply_to.voice
                or reply_to.video_note
                or reply_to.sticker
                or reply_to.animation
                or None
            )
            self.file_details = {"caption": reply_to.caption}

            if file_ is None:
                if reply_text := reply_to.text:
                    self.link = reply_text.split("\n", 1)[0].strip()
                else:
                    reply_to = None
            elif reply_to.document and (
                file_.mime_type == "application/x-bittorrent"
                or file_.file_name.endswith((".torrent", ".dlc", ".nzb"))
            ):
                self.link = await reply_to.download()
                file_ = None

        if (
            not self.link
            and file_ is None
            or is_telegram_link(self.link)
            and reply_to is None
            or file_ is None
            and not is_url(self.link)
            and not is_magnet(self.link)
            and not await aiopath.exists(self.link)
            and not is_rclone_path(self.link)
            and not is_gdrive_id(self.link)
            and not is_gdrive_link(self.link)
            and not is_mega_link(self.link)
        ):
            await send_message(
                self.message, COMMAND_USAGE["mirror"][0], COMMAND_USAGE["mirror"][1]
            )
            await self.remove_from_same_dir()
            await delete_links(self.message)
            return

        if len(self.link) > 0:
            LOGGER.info(self.link)

        try:
            await self.before_start()
        except Exception as e:
            await send_message(self.message, e)
            await self.remove_from_same_dir()
            await delete_links(self.message)
            return

        self._set_mode_engine()

        bypass_status = None
        if (
            not self.is_jd
            and not self.is_nzb
            and not self.is_qbit
            and not is_magnet(self.link)
            and not is_rclone_path(self.link)
            and not is_gdrive_link(self.link)
            and not self.link.endswith(".torrent")
            and file_ is None
            and not is_gdrive_id(self.link)
            and not is_mega_link(self.link)
        ):
            fast_bypass = _is_fast_bypass_host(self.link)
            content_type = None if fast_bypass else await get_content_type(self.link)
            if fast_bypass or content_type is None or re_match(r"text/html|text/plain", content_type):
                progress_task = None
                try:
                    original_link = self.link
                    mode_text = "leech" if self.is_leech else "mirror"
                    bypass_status = await send_message(
                        self.message,
                        "🔄 <b>Processing link...</b>\n"
                        "⚡ Bypass engine is checking your link...",
                    )
                    progress_task = bot_loop.create_task(
                        _animate_bypass_progress(bypass_status)
                    )

                    generated_link = await sync_to_async(direct_link_generator, self.link)
                    progress_task.cancel()
                    with suppress(CancelledError):
                        await progress_task
                    display_link = _extract_display_link(generated_link)
                    source, mirror = _detect_link_sources(original_link, display_link)

                    self.link = generated_link
                    if isinstance(self.link, tuple):
                        self.link, headers = self.link
                    elif isinstance(self.link, str):
                        LOGGER.info(f"Generated link: {self.link}")

                    if display_link:
                        await edit_message(
                            bypass_status,
                            _direct_link_found_text(source, mirror, display_link, mode_text),
                        )
                    else:
                        await edit_message(
                            bypass_status,
                            f"✅ <b>Direct link generated!</b>\n"
                            f"🌐 <b>Source:</b> {escape(source)}\n"
                            f"🪞 <b>Mirror Found:</b> {escape(mirror)}\n"
                            f"🚀 <b>Starting {escape(mode_text)}...</b>",
                        )
                except DirectDownloadLinkException as e:
                    if progress_task:
                        progress_task.cancel()
                        with suppress(CancelledError):
                            await progress_task
                    e = str(e)
                    if "This link requires a password!" not in e:
                        LOGGER.info(e)
                    if e.startswith("ERROR:"):
                        if bypass_status:
                            await edit_message(bypass_status, e)
                        else:
                            await send_message(self.message, e)
                        await self.remove_from_same_dir()
                        await delete_links(self.message)
                        return
                except Exception as e:
                    if progress_task:
                        progress_task.cancel()
                        with suppress(CancelledError):
                            await progress_task
                    if bypass_status:
                        await edit_message(bypass_status, e)
                    else:
                        await send_message(self.message, e)
                    await self.remove_from_same_dir()
                    await delete_links(self.message)
                    return

        await delete_links(self.message)
        if bypass_status:
            bot_loop.create_task(auto_delete_message(bypass_status, stime=10))

        if file_ is not None:
            await TelegramDownloadHelper(self).add_download(
                reply_to, f"{path}/", session
            )
        elif isinstance(self.link, dict):
            await add_direct_download(self, path)
        elif self.is_jd:
            await add_jd_download(self, path)
        elif self.is_qbit:
            await add_qb_torrent(self, path, ratio, seed_time)
        elif self.is_nzb:
            await add_nzb(self, path)
        elif is_rclone_path(self.link):
            await add_rclone_download(self, f"{path}/")
        elif is_gdrive_link(self.link) or is_gdrive_id(self.link):
            await add_gd_download(self, path)
        elif is_mega_link(self.link):
            await add_mega_download(self, f"{path}/")
        else:
            ussr = args["-au"]
            pssw = args["-ap"]
            if ussr or pssw:
                auth = f"{ussr}:{pssw}"
                headers += (
                    f" authorization: Basic {b64encode(auth.encode()).decode('ascii')}"
                )
            await add_aria2_download(self, path, headers, ratio, seed_time)


async def mirror(client, message):
    bot_loop.create_task(Mirror(client, message).new_event())


async def qb_mirror(client, message):
    bot_loop.create_task(Mirror(client, message, is_qbit=True).new_event())


async def jd_mirror(client, message):
    bot_loop.create_task(Mirror(client, message, is_jd=True).new_event())


async def nzb_mirror(client, message):
    bot_loop.create_task(Mirror(client, message, is_nzb=True).new_event())


async def leech(client, message):
    if Config.DISABLE_LEECH:
        await message.reply("The Leech command is currently disabled.")
        return
    bot_loop.create_task(Mirror(client, message, is_leech=True).new_event())


async def qb_leech(client, message):
    bot_loop.create_task(
        Mirror(client, message, is_qbit=True, is_leech=True).new_event()
    )


async def jd_leech(client, message):
    bot_loop.create_task(Mirror(client, message, is_leech=True, is_jd=True).new_event())


async def nzb_leech(client, message):
    bot_loop.create_task(
        Mirror(client, message, is_leech=True, is_nzb=True).new_event()
    )
