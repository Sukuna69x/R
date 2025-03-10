import os
import logging
import subprocess
from typing import Dict, List, Optional

import ffmpeg
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ConversationHandler,
)

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(name)

# States for conversation handler
UPLOAD, CHOOSE_QUALITY, RENAME, THUMBNAIL = range(4)

# Store user data
user_data_store = {}

# Bot token from BotFather
TOKEN = "7619297383:AAH80S9W4P1rB35Ygq31MJypanMui2fpCSQ"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_html(
        f"Hi {user.mention_html()}! I'm your Video Encoder Bot.\n\n"
        f"Here's what I can do:\n"
        f"- Encode videos to different qualities (480p, 720p, 1080p)\n"
        f"- Rename your video files\n"
        f"- Apply custom thumbnails\n\n"
        f"Send me a video file to get started!"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    await update.message.reply_text(
        "How to use this bot:\n\n"
        "1. Send a video file to begin\n"
        "2. Choose encoding quality (480p, 720p, 1080p)\n"
        "3. Provide a new name for the file (optional)\n"
        "4. Send a thumbnail image (optional)\n"
        "5. Wait for encoding to complete\n\n"
        "Commands:\n"
        "/start - Start the bot\n"
        "/help - Show this help message\n"
        "/cancel - Cancel the current operation"
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel and end the conversation."""
    user_id = update.effective_user.id
    if user_id in user_data_store:
        del user_data_store[user_id]
    
    await update.message.reply_text(
        "Current operation cancelled. Send me a video to start again."
    )
    return ConversationHandler.END

async def receive_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the video file sent by user."""
    user_id = update.effective_user.id
    video = update.message.video or update.message.document
    
    if not video:
        await update.message.reply_text("Please send a valid video file.")
        return ConversationHandler.END
    
    # Create user data entry
    user_data_store[user_id] = {
        "file_id": video.file_id,
        "original_filename": video.file_name,
        "mime_type": video.mime_type
    }
    
    # Download the file
    file = await context.bot.get_file(video.file_id)
    download_path = f"downloads/{user_id}_{video.file_name}"
    os.makedirs("downloads", exist_ok=True)
    await file.download_to_drive(download_path)
    
    user_data_store[user_id]["download_path"] = download_path
    
    # Ask for quality preference
    keyboard = [
        [
            InlineKeyboardButton("480p", callback_data="480p"),
            InlineKeyboardButton("720p", callback_data="720p"),
            InlineKeyboardButton("1080p", callback_data="1080p"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Video received! Select encoding quality:", reply_markup=reply_markup
    )
    
    return CHOOSE_QUALITY

async def quality_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle quality selection."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    quality = query.data
    user_data_store[user_id]["quality"] = quality
    
    await query.edit_message_text(
        f"Quality set to {quality}. Now, send me a new name for the file or /skip to keep the original name."
    )
    
    return RENAME

async def rename_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle file renaming."""
    user_id = update.effective_user.id
    
    if update.message.text == "/skip":
        # Keep original filename
        filename = user_data_store[user_id]["original_filename"]
        user_data_store[user_id]["new_filename"] = filename
        await update.message.reply_text(
            f"Keeping original filename: {filename}\n\n"
            f"Now, send me a thumbnail image or /skip to use auto-generated thumbnail."
        )
    else:
        # Use new filename
        new_filename = update.message.text
        
        # Add extension if missing
        if not new_filename.endswith((".mp4", ".avi", ".mkv", ".mov")):
            # Extract extension from original filename or default to .mp4
            original_ext = os.path.splitext(user_data_store[user_id]["original_filename"])[1]
            if not original_ext:
                original_ext = ".mp4"
            new_filename += original_ext
            
        user_data_store[user_id]["new_filename"] = new_filename
        await update.message.reply_text(
            f"File will be renamed to: {new_filename}\n\n"
            f"Now, send me a thumbnail image or /skip to use auto-generated thumbnail."
        )
    
    return THUMBNAIL

async def receive_thumbnail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle thumbnail image."""
    user_id = update.effective_user.id
    
    if update.message.text == "/skip":
        # Skip thumbnail
        user_data_store[user_id]["thumbnail_path"] = None
        await update.message.reply_text("Using auto-generated thumbnail. Starting encoding process...")
        return await start_encoding(update, context)
    
    if update.message.photo:
        # Get the largest photo size
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        thumbnail_path = f"thumbnails/{user_id}_thumb.jpg"
        os.makedirs("thumbnails", exist_ok=True)
        await file.download_to_drive(thumbnail_path)
        
        user_data_store[user_id]["thumbnail_path"] = thumbnail_path
        await update.message.reply_text("Thumbnail received! Starting encoding process...")
        return await start_encoding(update, context)
    else:
        await update.message.reply_text(
            "Please send a valid image for thumbnail or /skip to use auto-generated thumbnail."
        )
        return THUMBNAIL

async def start_encoding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Begin encoding process."""
    user_id = update.effective_user.id
    user_data = user_data_store[user_id]
    
    status_message = await update.message.reply_text("Encoding video... This may take a while.")
    
    # Create output directory
    os.makedirs("encoded", exist_ok=True)
    output_filename = f"encoded/{user_data['new_filename']}"
    
    # Get resolution based on quality selection
    resolution = {
        "480p": "854:480",
        "720p": "1280:720",
        "1080p": "1920:1080"
    }.get(user_data["quality"], "854:480")
    
    try:
        # Use FFmpeg to encode the video
        encode_cmd = [
            'ffmpeg',
            '-i', user_data["download_path"],
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '22',
            '-vf', f'scale={resolution}',
            '-c:a', 'aac',
            '-b:a', '128k',
            output_filename
        ]
        
        process = subprocess.Popen(
            encode_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            logger.error(f"Encoding failed: {stderr.decode()}")

        await status_message.edit_text("Encoding failed. Please try again with a different video.")
        return ConversationHandler.END
        
        # Apply custom thumbnail if provided
        if user_data.get("thumbnail_path"):
            thumb_cmd = [
                'ffmpeg',
                '-i', output_filename,
                '-i', user_data["thumbnail_path"],
                '-map', '0',
                '-map', '1',
                '-c', 'copy',
                '-disposition:v:1', 'attached_pic',
                f"encoded/temp_{user_data['new_filename']}"
            ]
            
            thumb_process = subprocess.Popen(
                thumb_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            thumb_stdout, thumb_stderr = thumb_process.communicate()
            
            if thumb_process.returncode == 0:
                os.replace(f"encoded/temp_{user_data['new_filename']}", output_filename)
        
        # Send the encoded video back to user
        await status_message.edit_text(f"Encoding completed! Uploading {user_data['quality']} video...")
        
        # Create thumbnail for Telegram
        thumbnail_file = None
        if user_data.get("thumbnail_path"):
            thumbnail_file = open(user_data["thumbnail_path"], "rb")
        
        with open(output_filename, "rb") as video_file:
            await context.bot.send_video(
                chat_id=update.effective_chat.id,
                video=video_file,
                thumb=thumbnail_file,
                filename=user_data["new_filename"],
                caption=f"Encoded to {user_data['quality']}"
            )
            
        if thumbnail_file:
            thumbnail_file.close()
            
        # Clean up
        try:
            os.remove(user_data["download_path"])
            os.remove(output_filename)
            if user_data.get("thumbnail_path"):
                os.remove(user_data["thumbnail_path"])
        except Exception as e:
            logger.error(f"Error cleaning up files: {e}")
        
        # Clean up user data
        del user_data_store[user_id]
        
        await status_message.edit_text("Video processing completed! Send me another video to process.")
        
    except Exception as e:
        logger.error(f"Error in encoding process: {e}")
        await status_message.edit_text("An error occurred during processing. Please try again.")
    
    return ConversationHandler.END

def main() -> None:
    """Start the bot."""
    # Create the Application
    application = Application.builder().token(TOKEN).build()

    # Add conversation handler
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.VIDEO | filters.Document.VIDEO, receive_video)],
        states={
            CHOOSE_QUALITY: [CallbackQueryHandler(quality_selection)],
            RENAME: [MessageHandler(filters.TEXT, rename_file)],
            THUMBNAIL: [
                MessageHandler(filters.PHOTO, receive_thumbnail),
                MessageHandler(filters.Regex("^/skip$"), receive_thumbnail)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(conv_handler)
    
    # Start the Bot
    application.run_polling()

if name == "main":
    main()
