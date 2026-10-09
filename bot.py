
import os
import uuid
import asyncio
import threading
import tempfile
import requests

from flask import Flask, send_file, abort
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# Settings: secrets Render se aayenge
BOT_TOKEN = os.environ.get("BOT_TOKEN")
TELEGRAM_USER_ID = os.environ.get("TELEGRAM_USER_ID")
INSTAGRAM_USER_ID = os.environ.get("INSTAGRAM_USER_ID")
INSTAGRAM_ACCESS_TOKEN = os.environ.get("INSTAGRAM_ACCESS_TOKEN")
GRAPH_API_VERSION = os.environ.get("GRAPH_API_VERSION", "v25.0")
INSTAGRAM_STORAGE_CHANNEL_ID = os.environ.get(
    "INSTAGRAM_STORAGE_CHANNEL_ID"
)
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing in Render Environment")

app = Flask(__name__)

# Temporary storage for images
media_store = {}
pending_posts = {}
TEMP_DIR = tempfile.gettempdir()


def is_authorized(user_id):
    return str(user_id) == str(TELEGRAM_USER_ID)


@app.get("/")
def home():
    return "Instagram Telegram Bot is running!"


@app.get("/media/<key>")
def serve_image(key):
    path = media_store.get(key)

    if not path or not os.path.isfile(path):
        abort(404)

    return send_file(path, mimetype="image/jpeg")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("You are not authorized.")
        return

    await update.message.reply_text(
        "Bot ready! Send me an image to prepare an Instagram post."
    )


async def receive_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user

    if not is_authorized(user.id):
        await update.message.reply_text("You are not authorized.")
        return

    photo = update.message.photo[-1]
    telegram_file = await context.bot.get_file(photo.file_id)

    key = uuid.uuid4().hex
    path = os.path.join(TEMP_DIR, f"{key}.jpg")

    await telegram_file.download_to_drive(custom_path=path)

    media_store[key] = path
    pending_posts[key] = {
        "user_id": user.id,
        "caption": update.message.caption or "",
    }

    # Optional: save a copy in your Telegram storage channel
    if INSTAGRAM_STORAGE_CHANNEL_ID:
        try:
            await context.bot.send_photo(
                chat_id=int(INSTAGRAM_STORAGE_CHANNEL_ID),
                photo=photo.file_id,
                caption="Instagram post backup",
            )
        except Exception as error:
            print("Storage channel copy failed:", str(error))

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Publish", callback_data=f"publish:{key}"
            ),
            InlineKeyboardButton(
                "Cancel", callback_data=f"cancel:{key}"
            ),
        ]
    ])

    await update.message.reply_text(
        "Image received. Publish this image on Instagram?",
        reply_markup=buttons,
    )


async def handle_button(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if not is_authorized(query.from_user.id):
        await query.edit_message_text("You are not authorized.")
        return

    action, key = query.data.split(":", 1)
    post = pending_posts.get(key)

    if not post or post["user_id"] != query.from_user.id:
        await query.edit_message_text("This post has expired.")
        return

    if action == "cancel":
        pending_posts.pop(key, None)
        await query.edit_message_text("Posting cancelled.")
        return

    if not all([
        INSTAGRAM_USER_ID,
        INSTAGRAM_ACCESS_TOKEN,
        PUBLIC_BASE_URL,
    ]):
        await query.edit_message_text(
            "Setup incomplete. Check Instagram settings in Render."
        )
        return

    image_url = f"{PUBLIC_BASE_URL}/media/{key}"
    caption = post["caption"]

    try:

        # Create an Instagram image container

        response = requests.post(

            f"https://graph.instagram.com/"

            f"{GRAPH_API_VERSION}/{INSTAGRAM_USER_ID}/media",

            data={

                "image_url": image_url,

                "caption": caption,

                "access_token": INSTAGRAM_ACCESS_TOKEN,

            },

            timeout=60,

        )

        result = response.json()

        if not response.ok or "id" not in result:

            raise RuntimeError(

                result.get("error", {}).get(

                    "message", "Could not create Instagram post"

                )

            )

        creation_id = result["id"]

        # Publish the image

        response = requests.post(

            f"https://graph.instagram.com/"

            f"{GRAPH_API_VERSION}/{INSTAGRAM_USER_ID}/media_publish",

            data={

                "creation_id": creation_id,

                "access_token": INSTAGRAM_ACCESS_TOKEN,

            },

            timeout=60,

        )

        result = response.json()

        if not response.ok or "id" not in result:

            raise RuntimeError(

                result.get("error", {}).get(

                    "message", "Instagram could not publish the image"

                )

            )

        pending_posts.pop(key, None)

        await query.edit_message_text(

            "Image published successfully on Instagram!"

        )

    except Exception as error:

        print("Instagram publishing failed:", str(error))

        await query.edit_message_text(

            f"Publishing failed: {str(error)}"

        )

def run_web_server():

    port = int(os.environ.get("PORT", "10000"))

    app.run(host="0.0.0.0", port=port, use_reloader=False)

def main():

    threading.Thread(

        target=run_web_server, daemon=True

    ).start()

    bot = Application.builder().token(BOT_TOKEN).build()

    bot.add_handler(CommandHandler("start", start))

    bot.add_handler(

        MessageHandler(filters.PHOTO, receive_photo)

    )

    bot.add_handler(CallbackQueryHandler(handle_button))

    bot.run_polling()

if __name__ == "__main__":

    main()
