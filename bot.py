import os
import openai
import redis
import json
import tempfile
from dotenv import load_dotenv
from pydub import AudioSegment
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# Load environment variables
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Connect to Redis
rdb = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# Session handlers
def load_session(user_id):
    data = rdb.get(f"session:{user_id}")
    if data:
        return json.loads(data)
    return []

def save_session(user_id, session):
    # Save only last 10 messages to limit context
    rdb.set(f"session:{user_id}", json.dumps(session[-10:]))

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("Reset Memory", callback_data="reset")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Hi! I'm your AI assistant. Ask me anything.", reply_markup=reply_markup)

# Reset command
async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rdb.delete(f"session:{user_id}")
    await update.message.reply_text("✅ Memory cleared.")

# Callback for inline buttons
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "reset":
        await reset(update, context)

# Handle text messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_input = update.message.text

    session = load_session(user_id)
    session.append({"role": "user", "content": user_input})

    try:
        client = openai.OpenAI()
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": "You are a helpful assistant."}] + session,
        )
        reply = response.choices[0].message.content.strip()
        session.append({"role": "assistant", "content": reply})
        save_session(user_id, session)
        await update.message.reply_text(reply)
    except Exception as e:
        print("OpenAI Error:", e)
        await update.message.reply_text("⚠️ Error occurred. Try again.")

# Handle voice input
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    voice = update.message.voice

    file = await context.bot.get_file(voice.file_id)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as temp_ogg:
        await file.download_to_drive(custom_path=temp_ogg.name)
        ogg_path = temp_ogg.name

    wav_path = ogg_path.replace(".ogg", ".wav")
    audio = AudioSegment.from_ogg(ogg_path)
    audio.export(wav_path, format="wav")

    try:
        client = openai.OpenAI()
        with open(wav_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
            user_text = transcript.text

            class FakeMessage:
                def __init__(self, text):
                    self.text = text
                    self.from_user = update.message.from_user
                    self.chat = update.message.chat
                async def reply_text(self, txt):
                    await update.message.reply_text(txt)

            fake_update = Update(update.update_id, message=FakeMessage(user_text))
            await handle_message(fake_update, context)
    except Exception as e:
        print("Whisper Error:", e)
        await update.message.reply_text("❌ Couldn't process voice message.")

# Main entrypoint
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("🤖 Bot is running...")
    app.run_polling()
