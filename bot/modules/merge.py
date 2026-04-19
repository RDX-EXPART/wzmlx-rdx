import os
import shutil
import tempfile
import asyncio
from asyncio.subprocess import PIPE
from asyncio import create_subprocess_exec, Lock
from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import bot, LOGGER, user_dict, manage_dict, global_lock, user_locks
from bot.helper.telegram_helper.message_utils import sendMessage, editMessage
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.ext_utils.bot_utils import new_task
from bot.helper.ext_utils.files_utils import clean_target
from bot.helper.ext_utils.media_utils import get_media_info, get_path_size
from bot.helper.mirror_utils.status_utils.ffmpeg_status import FfmpegStatus

# গ্লোবাল ভ্যারিয়েবল
if not hasattr(bot, 'merge_data'):
    bot.merge_data = {}

async def merge_handler(client, message):
    """ /vt বা /leech -vt কমান্ড হ্যান্ডেল করে """
    user_id = message.from_user.id
    replied = message.reply_to_message

    if not replied:
        return await sendMessage(message, "এক বা একাধিক ভিডিও/অডিও ফাইলে রিপ্লাই দিয়ে /vt কমান্ড দিন")

    # মিডিয়া গ্রুপ সাপোর্ট
    if replied.media_group_id:
        try:
            media_messages = await client.get_media_group(message.chat.id, replied.id)
            files = [msg for msg in media_messages if msg.video or msg.audio]
        except Exception as e:
            LOGGER.error(f"Media group error: {e}")
            files = []
    else:
        files = [replied] if replied.video or replied.audio else []

    if not files:
        return await sendMessage(message, "কোনো ভিডিও বা অডিও ফাইল পাওয়া যায়নি")

    # ডাটা স্টোর করা
    bot.merge_data[message.id] = {
        'files': files,
        'chat_id': message.chat.id,
        'user_id': user_id,
        'message': message
    }

    # স্ক্রিনশটের মতো মেনু
    buttons = ButtonMaker()
    has_video = any(f.video for f in files)
    has_audio = any(f.audio for f in files)
    video_count = sum(1 for f in files if f.video)

    if video_count >= 2:
        buttons.ibutton("Video + Video", f"merge vv {message.id}")
    if has_video and has_audio:
        buttons.ibutton("Video + Audio", f"merge va {message.id}")

    buttons.ibutton("Cancel", f"merge cancel {message.id}")
    btn_markup = buttons.build_menu(2)

    if not has_video and not has_audio:
        del bot.merge_data[message.id]
        return await sendMessage(message, "শুধু ভিডিও বা অডিও ফাইল সাপোর্টেড")

    await sendMessage(
        message,
        f"**VIDEOS TOOL SETTINGS**\nName: **Default**\n\n*Time Out: 5m00s*\n\nসিলেক্টেড: {len(files)} টি ফাইল",
        btn_markup
    )

@bot.on_callback_query(filters.regex(r"^merge"))
async def merge_callback(client, callback_query):
    data = callback_query.data.split()
    if len(data) < 3:
        return await callback_query.answer("Invalid data", show_alert=True)

    action = data[1]
    msg_id = int(data[2])

    if msg_id not in bot.merge_data:
        return await callback_query.answer("টাইম আউট! আবার /vt দিন", show_alert=True)

    merge_data = bot.merge_data[msg_id]
    await callback_query.answer()

    if action == "cancel":
        del bot.merge_data[msg_id]
        return await editMessage(callback_query.message, "ক্যান্সেল করা হয়েছে ❌")

    elif action == "vv": # Video + Video
        videos = [f for f in merge_data['files'] if f.video]
        if len(videos) < 2:
            return await editMessage(callback_query.message, "Video + Video এর জন্য কমপক্ষে ২টা ভিডিও লাগবে")
        await editMessage(callback_query.message, "ভিডিও মার্জ শুরু হচ্ছে... ⏳")
        await process_video_video(client, callback_query.message, merge_data, videos)

    elif action == "va": # Video + Audio
        videos = [f for f in merge_data['files'] if f.video]
        audios = [f for f in merge_data['files'] if f.audio]
        if not videos or not audios:
            return await editMessage(callback_query.message, "Video + Audio এর জন্য ১টা ভিডিও + ১টা অডিও লাগবে")
        await editMessage(callback_query.message, "অডিও রিপ্লেস হচ্ছে... ⏳")
        await process_video_audio(client, callback_query.message, merge_data, videos[0], audios[0])

    if msg_id in bot.merge_data:
        del bot.merge_data[msg_id]

async def process_video_video(client, message, data, videos):
    """ একাধিক ভিডিও জোড়া লাগানো """
    user_id = data['user_id']
    chat_id = data['chat_id']
    temp_dir = tempfile.mkdtemp(prefix=f'wzmlx_vv_{user_id}_')

    try:
        # ডাউনলোড
        file_paths = []
        for i, video in enumerate(videos):
            await editMessage(message, f"ডাউনলোড: {i+1}/{len(videos)} ⬇️")
            path = await video.download(file_name=os.path.join(temp_dir, f"v{i}.mp4"))
            file_paths.append(path)

        # ffmpeg concat list
        list_path = os.path.join(temp_dir, "list.txt")
        with open(list_path, 'w', encoding='utf-8') as f:
            for path in file_paths:
                f.write(f"file '{os.path.basename(path)}'\n")

        output_path = os.path.join(temp_dir, "merged_video.mp4")
        await editMessage(message, "মার্জ হচ্ছে... এতে সময় লাগবে 🎬")

        # সব ভিডিও 1080p তে রিসাইজ করে মার্জ - ফরম্যাট আলাদা হলেও কাজ করবে
        cmd = [
            'ffmpeg', '-hide_banner', '-f', 'concat', '-safe', '0', '-i', list_path,
            '-vf', 'scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2',
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-c:a', 'aac', '-b:a', '192k',
            output_path, '-y'
        ]

        proc = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
        _, stderr = await proc.communicate()

        if os.path.exists(output_path) and os.path.getsize(output_path) > 100000:
            await editMessage(message, "আপলোড হচ্ছে... ⬆️")
            await client.send_video(chat_id, output_path, caption="**Video + Video Merged** ✅\nBy WZML-X")
            await message.delete()
        else:
            LOGGER.error(f"FFmpeg Error: {stderr.decode()}")
            await editMessage(message, "মার্জ ফেইলড! লগ চেক করুন বা অন্য ভিডিও ট্রাই করুন ❌")

    except Exception as e:
        LOGGER.error(f"VV Error: {e}")
        await editMessage(message, f"এরর হয়েছে: {str(e)}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

async def process_video_audio(client, message, data, video, audio):
    """ ভিডিওর অডিও রিপ্লেস করা """
    user_id = data['user_id']
    chat_id = data['chat_id']
    temp_dir = tempfile.mkdtemp(prefix=f'wzmlx_va_{user_id}_')

    try:
        await editMessage(message, "ভিডিও ডাউনলোড হচ্ছে... ⬇️")
        v_path = await video.download(file_name=os.path.join(temp_dir, "video.mp4"))
        await editMessage(message, "অডিও ডাউনলোড হচ্ছে... ⬇️")
        a_path = await audio.download(file_name=os.path.join(temp_dir, "audio.m4a"))

        output_path = os.path.join(temp_dir, "output.mp4")
        await editMessage(message, "অডিও রিপ্লেস হচ্ছে... 🎵")

        # ভিডিওর পুরানো অডিও বাদ দিয়ে নতুন অডিও
        cmd = [
            'ffmpeg', '-hide_banner', '-i', v_path, '-i', a_path,
            '-c:v', 'copy', '-c:a', 'aac', '-map', '0:v:0', '-map', '1:a:0',
            '-shortest', output_path, '-y'
        ]

        proc = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
        _, stderr = await proc.communicate()

        if os.path.exists(output_path) and os.path.getsize(output_path) > 100000:
            await editMessage(message, "আপলোড হচ্ছে... ⬆️")
            await client.send_video(chat_id, output_path, caption="**Video + Audio Merged** ✅\nBy WZML-X")
            await message.delete()
        else:
            LOGGER.error(f"FFmpeg Error: {stderr.decode()}")
            await editMessage(message, "মার্জ ফেইলড! ❌")

    except Exception as e:
        LOGGER.error(f"VA Error: {e}")
        await editMessage(message, f"এরর হয়েছে: {str(e)}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

# কমান্ড রেজিস্টার
bot.add_handler(filters.command(['vt', 'videotool']), merge_handler)
