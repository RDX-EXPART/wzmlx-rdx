import os
import shutil
import tempfile
import asyncio
from asyncio.subprocess import PIPE
from asyncio import create_subprocess_exec
from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import bot, LOGGER, user_dict, manage_dict
from bot.helper.telegram_helper.message_utils import sendMessage, editMessage
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.ext_utils.bot_utils import new_task
from bot.helper.ext_utils.files_utils import clean_target

# WZML-X এর জন্য গ্লোবাল স্টোর
if not hasattr(bot, 'wzmlx_vt_data'):
    bot.wzmlx_vt_data = {}

async def video_tool_handler(client, message):
    """ /leech -vt কমান্ড হ্যান্ডেল করে """
    user_id = message.from_user.id
    replied = message.reply_to_message

    if not replied:
        return await sendMessage(message, "**VIDEOS TOOL SETTINGS**\n\nভিডিও/অডিও ফাইলে রিপ্লাই দিয়ে `/leech -vt` দিন")

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

    # ডাটা স্টোর
    bot.wzmlx_vt_data[message.id] = {
        'files': files,
        'chat_id': message.chat.id,
        'user_id': user_id,
        'message': message
    }

    # স্ক্রিনশটের মতো মেনু
    buttons = ButtonMaker()
    has_video = any(f.video for f in files)
    video_count = sum(1 for f in files if f.video)
    has_audio = any(f.audio for f in files)

    if video_count >= 2:
        buttons.ibutton("Video + Video", f"wvt_vv {message.id}")
    if has_video and has_audio:
        buttons.ibutton("Video + Audio", f"wvt_va {message.id}")

    buttons.ibutton("Cancel", f"wvt_cancel {message.id}")
    btn_markup = buttons.build_menu(2)

    if not has_video and not has_audio:
        del bot.wzmlx_vt_data[message.id]
        return await sendMessage(message, "শুধু ভিডিও বা অডিও ফাইল সাপোর্টেড")

    await sendMessage(
        message,
        f"**VIDEOS TOOL SETTINGS**\nName: **Default**\n\n*Time Out: 5m00s*\n\nসিলেক্টেড: {len(files)} টি ফাইল",
        btn_markup
    )

@bot.on_callback_query(filters.regex(r"^wvt"))
async def video_tool_callback(client, callback_query):
    data = callback_query.data.split()
    if len(data) < 3:
        return await callback_query.answer("Invalid data", show_alert=True)

    action = data[1]
    msg_id = int(data[2])

    if msg_id not in bot.wzmlx_vt_data:
        return await callback_query.answer("টাইম আউট! আবার /leech -vt দিন", show_alert=True)

    vt_data = bot.wzmlx_vt_data[msg_id]
    await callback_query.answer()

    if action == "cancel":
        del bot.wzmlx_vt_data[msg_id]
        return await editMessage(callback_query.message, "ক্যান্সেল করা হয়েছে ❌")

    elif action == "vv": # Video + Video
        videos = [f for f in vt_data['files'] if f.video]
        if len(videos) < 2:
            return await editMessage(callback_query.message, "কমপক্ষে ২টা ভিডিও লাগবে")
        await editMessage(callback_query.message, "ভিডিও মার্জ শুরু হচ্ছে... ⏳")
        await process_video_video(client, callback_query.message, vt_data, videos)

    elif action == "va": # Video + Audio
        videos = [f for f in vt_data['files'] if f.video]
        audios = [f for f in vt_data['files'] if f.audio]
        if not videos or not audios:
            return await editMessage(callback_query.message, "১টা ভিডিও + ১টা অডিও লাগবে")
        await editMessage(callback_query.message, "অডিও রিপ্লেস হচ্ছে... ⏳")
        await process_video_audio(client, callback_query.message, vt_data, videos[0], audios[0])

    if msg_id in bot.wzmlx_vt_data:
        del bot.wzmlx_vt_data[msg_id]

async def process_video_video(client, message, data, videos):
    """ একাধিক ভিডিও জোড়া লাগানো """
    chat_id = data['chat_id']
    temp_dir = tempfile.mkdtemp(prefix=f'wzmlx_vv_')

    try:
        file_paths = []
        for i, video in enumerate(videos):
            await editMessage(message, f"ডাউনলোড: {i+1}/{len(videos)} ⬇️")
            path = await video.download(file_name=os.path.join(temp_dir, f"v{i}.mp4"))
            file_paths.append(path)

        list_path = os.path.join(temp_dir, "list.txt")
        with open(list_path, 'w', encoding='utf-8') as f:
            for path in file_paths:
                f.write(f"file '{os.path.basename(path)}'\n")

        output_path = os.path.join(temp_dir, "merged.mp4")
        await editMessage(message, "মার্জ হচ্ছে... 🎬")

        # সব ভিডিও 1080p তে রিসাইজ করে মার্জ - ফরম্যাট আলাদা হলেও চলবে
        cmd = [
            'ffmpeg', '-hide_banner', '-f', 'concat', '-safe', '0', '-i', list_path,
            '-vf', 'scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1',
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-c:a', 'aac', '-b:a', '192k',
            output_path, '-y'
        ]

        proc = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
        _, stderr = await proc.communicate()

        if os.path.exists(output_path) and os.path.getsize(output_path) > 100000:
            await editMessage(message, "আপলোড হচ্ছে... ⬆️")
            await client.send_video(chat_id, output_path, caption="**Video + Video Merged** ✅")
            await message.delete()
        else:
            LOGGER.error(f"FFmpeg VV Error: {stderr.decode()}")
            await editMessage(message, "মার্জ ফেইলড! অন্য ভিডিও ট্রাই করুন ❌")

    except Exception as e:
        LOGGER.error(f"VV Exception: {e}")
        await editMessage(message, f"এরর: {str(e)}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

async def process_video_audio(client, message, data, video, audio):
    """ ভিডিওর অডিও রিপ্লেস করা """
    chat_id = data['chat_id']
    temp_dir = tempfile.mkdtemp(prefix=f'wzmlx_va_')

    try:
        await editMessage(message, "ভিডিও ডাউনলোড হচ্ছে... ⬇️")
        v_path = await video.download(file_name=os.path.join(temp_dir, "video.mp4"))
        await editMessage(message, "অডিও ডাউনলোড হচ্ছে... ⬇️")
        a_path = await audio.download(file_name=os.path.join(temp_dir, "audio.m4a"))

        output_path = os.path.join(temp_dir, "output.mp4")
        await editMessage(message, "অডিও রিপ্লেস হচ্ছে... 🎵")

        cmd = [
            'ffmpeg', '-hide_banner', '-i', v_path, '-i', a_path,
            '-c:v', 'copy', '-c:a', 'aac', '-map', '0:v:0', '-map', '1:a:0',
            '-shortest', output_path, '-y'
        ]

        proc = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
        _, stderr = await proc.communicate()

        if os.path.exists(output_path) and os.path.getsize(output_path) > 100000:
            await editMessage(message, "আপলোড হচ্ছে... ⬆️")
            await client.send_video(chat_id, output_path, caption="**Video + Audio Merged** ✅")
            await message.delete()
        else:
            LOGGER.error(f"FFmpeg VA Error: {stderr.decode()}")
            await editMessage(message, "মার্জ ফেইলড! ❌")

    except Exception as e:
        LOGGER.error(f"VA Exception: {e}")
        await editMessage(message, f"এরর: {str(e)}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

# WZML-X এর জন্য এক্সপোর্ট
merge_handler = video_tool_handler
