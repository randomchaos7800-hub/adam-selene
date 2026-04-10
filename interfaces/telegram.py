"""Telegram interface for Adam Selene.

This is just one adapter. The relay handles all conversation logic.
Run with: python -m interfaces.telegram
"""

import asyncio
import base64
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Set up paths and load environment
PROJECT_ROOT = Path(__file__).parent.parent
SECRETS_PATH = PROJECT_ROOT / "config" / "secrets.env"
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.json"

if SECRETS_PATH.exists():
    load_dotenv(SECRETS_PATH, override=True)

sys.path.insert(0, str(PROJECT_ROOT))

from relay import config
from relay.relay import get_relay
from relay.heartbeat import Heartbeat
from relay.telegram_sender import mark_owner_responded
from memory import extraction, storage

logger = logging.getLogger(__name__)


def load_settings() -> dict:
    """Load settings from config file."""
    if SETTINGS_PATH.exists():
        return json.loads(SETTINGS_PATH.read_text())
    return {}


SETTINGS = load_settings()
ALLOWED_USERS = SETTINGS.get("allowed_telegram_users", [])
EXTRACTION_TIMEOUT = SETTINGS.get("extraction", {}).get("idle_timeout_seconds", 120)
HEARTBEAT_IDLE_MINUTES = SETTINGS.get("heartbeat", {}).get("idle_minutes", 15)

# Extraction timer state
_extraction_timers: dict[str, asyncio.Task] = {}

# Heartbeat instance
_heartbeat: Heartbeat = None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    await update.message.reply_text(
        f"hey. I'm {config.agent_name()}.\n\n"
        "Just talk to me naturally. I'll learn about your life through conversation.\n\n"
        "Commands:\n"
        "/status - what's in my memory\n"
        "/entities - list known entities\n"
        "/done - force extraction now\n"
        "/heartbeat - toggle heartbeat on/off"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command."""
    mem_status = storage.status()

    if not mem_status.get("initialized"):
        await update.message.reply_text("Memory not initialized. Something's wrong.")
        return

    text = (
        f"Memory status:\n"
        f"- Entities: {mem_status['entity_count']}\n"
        f"- Active facts: {mem_status['active_fact_count']}\n"
        f"- Daily notes: {mem_status['daily_note_count']}\n"
        f"- Last note: {mem_status['last_note_date'] or 'none'}\n"
        f"- Prompt version: {mem_status['prompt_version']}\n"
        f"- Experiments logged: {mem_status['experiment_count']}"
    )
    await update.message.reply_text(text)


async def entities_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /entities command."""
    entities = storage.list_entities_by_category()

    if not entities:
        await update.message.reply_text("No entities in memory yet.")
        return

    by_cat = {}
    for e in entities:
        cat = e["category"]
        if cat not in by_cat:
            by_cat[cat] = []
        by_cat[cat].append(e["name"])

    lines = ["Entities in memory:"]
    for cat, names in by_cat.items():
        lines.append(f"\n{cat.title()}:")
        for name in names:
            lines.append(f"  - {name}")

    await update.message.reply_text("\n".join(lines))


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /done command - force extraction now."""
    user_id = str(update.effective_user.id)

    if user_id in _extraction_timers:
        _extraction_timers[user_id].cancel()
        del _extraction_timers[user_id]

    await run_extraction(user_id)
    await update.message.reply_text("Extraction complete.")


async def heartbeat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /heartbeat command - toggle heartbeat."""
    global _heartbeat

    if _heartbeat is None:
        await update.message.reply_text("Heartbeat not initialized.")
        return

    if _heartbeat.paused:
        _heartbeat.resume()
        await update.message.reply_text("Heartbeat resumed.")
    else:
        _heartbeat.pause()
        await update.message.reply_text("Heartbeat paused.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle regular text messages -- pass to relay."""
    user_id = update.effective_user.id
    chat_type = update.message.chat.type  # 'private', 'group', or 'supergroup'

    # Check authorization
    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        logger.warning(f"Unauthorized user: {user_id}")
        # Don't respond in groups to avoid spam
        if chat_type == 'private':
            await update.message.reply_text("Sorry, I don't know you.")
        return

    user_id_str = config.owner_user_id()  # canonical ID -- maps all platforms to same session
    message_text = update.message.text

    # In group chats, only respond if mentioned or replied to
    if chat_type in ['group', 'supergroup']:
        bot_username = context.bot.username
        is_mentioned = f"@{bot_username}" in message_text if bot_username else False
        is_reply = update.message.reply_to_message and update.message.reply_to_message.from_user.is_bot

        if not (is_mentioned or is_reply):
            # Not for us, ignore
            return

        # Remove the @mention from the message
        if is_mentioned and bot_username:
            message_text = message_text.replace(f"@{bot_username}", "").strip()

    # Mark that owner responded (conversation state tracking)
    mark_owner_responded()

    try:
        relay = get_relay()
        response = relay.respond(message_text, user_id_str, interface="telegram")
    except Exception as e:
        logger.error(f"Error getting response: {e}")
        await update.message.reply_text(f"Error: {e}")
        return

    # Telegram has a 4096 char limit
    if len(response) > 4000:
        # Split into chunks
        for i in range(0, len(response), 4000):
            await update.message.reply_text(response[i:i+4000])
    else:
        await update.message.reply_text(response)

    # Reset extraction timer
    await reset_extraction_timer(user_id_str)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photo messages -- agent can see images."""
    user_id = update.effective_user.id

    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        logger.warning(f"Unauthorized user: {user_id}")
        await update.message.reply_text("Sorry, I don't know you.")
        return

    user_id_str = config.owner_user_id()  # canonical ID
    caption = update.message.caption or ""

    # Mark that owner responded
    mark_owner_responded()

    try:
        # Get the largest photo size
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()

        # Download photo
        photo_bytes = await photo_file.download_as_bytearray()

        # Convert to base64
        photo_base64 = base64.b64encode(photo_bytes).decode('utf-8')

        # Build image data for Claude API
        images = [{
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": photo_base64
            }
        }]

        # Send to relay with image
        relay = get_relay()
        message_text = caption if caption else "What do you see in this image?"
        response = relay.respond(message_text, user_id_str, images=images, interface="telegram")

        # Send response
        if len(response) > 4000:
            for i in range(0, len(response), 4000):
                await update.message.reply_text(response[i:i+4000])
        else:
            await update.message.reply_text(response)

        logger.info(f"Processed photo from {user_id_str}")

    except Exception as e:
        logger.error(f"Error processing photo: {e}")
        await update.message.reply_text(f"Error processing image: {e}")
        return

    # Reset extraction timer
    await reset_extraction_timer(user_id_str)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle document messages (PDFs, text files, etc.)."""
    user_id = update.effective_user.id

    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        logger.warning(f"Unauthorized user: {user_id}")
        await update.message.reply_text("Sorry, I don't know you.")
        return

    user_id_str = config.owner_user_id()  # canonical ID
    caption = update.message.caption or ""
    document = update.message.document

    # Mark that owner responded
    mark_owner_responded()

    try:
        # Check file size (limit to 20MB)
        if document.file_size > 20 * 1024 * 1024:
            await update.message.reply_text("File too large. Please send files under 20MB.")
            return

        # Download document
        doc_file = await document.get_file()
        doc_bytes = await doc_file.download_as_bytearray()

        # Determine file type
        mime_type = document.mime_type or "application/octet-stream"
        file_name = document.file_name or "document"

        images = []

        # Handle PDFs (Claude can read PDFs as images)
        if mime_type == "application/pdf" or file_name.lower().endswith('.pdf'):
            # For PDFs, send as document type
            doc_base64 = base64.b64encode(doc_bytes).decode('utf-8')
            images = [{
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": doc_base64
                }
            }]
            message_text = caption if caption else "What's in this PDF?"

        # Handle images sent as documents
        elif mime_type.startswith("image/"):
            doc_base64 = base64.b64encode(doc_bytes).decode('utf-8')
            images = [{
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime_type,
                    "data": doc_base64
                }
            }]
            message_text = caption if caption else "What do you see in this image?"

        # Handle text files
        elif mime_type.startswith("text/") or file_name.endswith(('.txt', '.md', '.py', '.js', '.json', '.csv')):
            try:
                text_content = doc_bytes.decode('utf-8')
                message_text = f"{caption}\n\n[File: {file_name}]\n{text_content}" if caption else f"[File: {file_name}]\n{text_content}"
            except Exception:
                await update.message.reply_text("Could not read text file. Please check encoding.")
                return
        else:
            await update.message.reply_text(f"Unsupported file type: {mime_type}. I can read PDFs, images, and text files.")
            return

        # Send to relay
        relay = get_relay()
        response = relay.respond(message_text, user_id_str, images=images if images else None, interface="telegram")

        # Send response
        if len(response) > 4000:
            for i in range(0, len(response), 4000):
                await update.message.reply_text(response[i:i+4000])
        else:
            await update.message.reply_text(response)

        logger.info(f"Processed document {file_name} from {user_id_str}")

    except Exception as e:
        logger.error(f"Error processing document: {e}")
        await update.message.reply_text(f"Error processing document: {e}")
        return

    # Reset extraction timer
    await reset_extraction_timer(user_id_str)


async def reset_extraction_timer(user_id: str) -> None:
    """Reset the extraction idle timer."""
    global _extraction_timers

    if user_id in _extraction_timers:
        _extraction_timers[user_id].cancel()

    async def timer_callback():
        try:
            await asyncio.sleep(EXTRACTION_TIMEOUT)
            await run_extraction(user_id)
        except asyncio.CancelledError:
            pass

    _extraction_timers[user_id] = asyncio.create_task(timer_callback())


async def run_extraction(user_id: str) -> None:
    """Run extraction on recent conversation."""
    relay = get_relay()
    conversation_text = relay.get_conversation_text(user_id, hours=4)

    if not conversation_text or len(conversation_text.strip()) < 20:
        logger.info(f"Skipping extraction for {user_id} - conversation too short")
        return

    logger.info(f"Running extraction for user {user_id}")

    try:
        result = extraction.run(conversation_text)

        facts_added = len(result.get("processing", {}).get("added_facts", []))
        entities_added = len(result.get("processing", {}).get("added_entities", []))

        if facts_added > 0 or entities_added > 0:
            logger.info(f"Extraction complete: {facts_added} facts, {entities_added} entities")

    except Exception as e:
        logger.error(f"Extraction error: {e}")

    if user_id in _extraction_timers:
        del _extraction_timers[user_id]


def main():
    """Start the Telegram bot with heartbeat."""
    global _heartbeat

    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO
    )

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN not set")
        sys.exit(1)

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("Error: OPENROUTER_API_KEY not set")
        sys.exit(1)

    # Initialize relay (and memory)
    try:
        relay = get_relay()
        logger.info("Relay initialized")
    except Exception as e:
        print(f"Error initializing relay: {e}")
        sys.exit(1)

    # Initialize heartbeat
    try:
        # Pass the owner user_id so heartbeat reflects on the right conversations
        hb_user_id = config.owner_user_id()  # canonical ID matches session key
        _heartbeat = Heartbeat(idle_minutes=HEARTBEAT_IDLE_MINUTES, user_id=hb_user_id)
        logger.info(f"Heartbeat initialized (idle threshold: {HEARTBEAT_IDLE_MINUTES}min)")
    except Exception as e:
        logger.warning(f"Heartbeat init failed (non-fatal): {e}")

    # Build application
    application = Application.builder().token(token).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("entities", entities_command))
    application.add_handler(CommandHandler("done", done_command))
    application.add_handler(CommandHandler("heartbeat", heartbeat_command))

    # Message handlers - order matters (more specific first)
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start heartbeat as background task, cancel cleanly on shutdown
    _heartbeat_task = None

    async def post_init(app):
        nonlocal _heartbeat_task
        if _heartbeat:
            _heartbeat_task = asyncio.create_task(_heartbeat.start())
            logger.info("Heartbeat background task started")

    async def post_shutdown(app):
        if _heartbeat_task and not _heartbeat_task.done():
            _heartbeat_task.cancel()
            try:
                await _heartbeat_task
            except asyncio.CancelledError:
                pass
            logger.info("Heartbeat task cancelled cleanly")

    application.post_init = post_init
    application.post_shutdown = post_shutdown

    print(f"{config.agent_name()} is running. Talk to it on Telegram.")
    print("Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
