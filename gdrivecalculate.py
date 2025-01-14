import shutil
import time
import threading
import logging
import os
from collections import deque
from telethon import TelegramClient, events
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive

# --- Configuration ---
logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')
API_ID = 29001415
API_HASH = '92152fd62ffbff12f057edc057f978f1'
BOT_TOKEN = '7505846620:AAFvv-sFybGfFILS-dRC8l7ph_0rqIhDgRM'
MAX_QUEUE_SIZE = 5  # Maximum number of files in the queue
STORAGE_THRESHOLD = 1024 * 1024 * 1024 * 10  # 10 GB storage threshold
MAX_CONCURRENT_THREADS = 2  # Maximum number of concurrent threads
CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'token.pickle'
ADMIN_USER_ID = 6472109162  # Replace with your Telegram user ID
FLOOD_TIMEOUT = 10  # Seconds to wait before allowing a user to send another file

# --- Telegram Client ---
client = TelegramClient('your_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
lock = threading.Lock()  # Lock for thread safety

# --- Global Variables ---
processing_files = {}  # Dictionary to store file IDs and their processing status
file_queue = deque(maxlen=MAX_QUEUE_SIZE)  # Queue to store files waiting to be processed
queue_condition = threading.Condition()  # Condition variable for queue synchronization
active_threads = 0  # Number of currently active threads
drive = None  # Initialize Google Drive client later
last_user_message = {}  # Store the last message time for each user

# --- Helper Functions ---

def check_disk_space(file_size):
    """Checks if there is enough free disk space."""
    total, used, free = shutil.disk_usage("/")
    return free - file_size > STORAGE_THRESHOLD


def check_flood(user_id):
    """Checks if the user is flooding the bot."""
    current_time = time.time()
    if user_id in last_user_message:
        if current_time - last_user_message[user_id] < FLOOD_TIMEOUT:
            return False
    last_user_message[user_id] = current_time
    return True


def process_file(event):
    """Downloads and uploads the file."""
    global active_threads, drive
    try:
        file_id = event.media.document.id
        processing_files[file_id] = "Downloading..."

        client.loop.run_until_complete(event.respond(processing_files[file_id]))
        file_path = client.loop.run_until_complete(client.download_media(event.media))

        if processing_files.get(file_id) == "Cancelled":
            return

        processing_files[file_id] = "Uploading..."
        client.loop.run_until_complete(event.respond(processing_files[file_id]))

        # Wait for credentials and initialize Google Drive client
        with queue_condition:
            while drive is None:
                queue_condition.wait()

        gfile = drive.CreateFile({'title': os.path.basename(file_path)})
        gfile.SetContentFile(file_path)
        gfile.Upload()

        if processing_files.get(file_id) != "Cancelled":
            client.loop.run_until_complete(event.respond('File uploaded to Google Drive!'))

    except Exception as e:
        logging.error(f"Error processing file: {event.file.name}\n{e}")
        try:
            client.loop.run_until_complete(event.respond(f"An error occurred: {e}"))
            # Send error to admin
            error_message = f"Error processing file: {event.file.name}\n\n{e}"
            client.loop.run_until_complete(client.send_message(ADMIN_USER_ID, error_message))
        except Exception as e2:
            logging.error(f"Error sending error message: {e2}")
    finally:
        if file_id in processing_files:
            del processing_files[file_id]
        with queue_condition:
            active_threads -= 1
            queue_condition.notify()


def worker_thread():
    """Worker thread function to process files from the queue."""
    global active_threads
    with queue_condition:
        while True:
            while not file_queue or active_threads >= MAX_CONCURRENT_THREADS:
                queue_condition.wait()

            event, _ = file_queue.popleft()
            active_threads += 1
            thread = threading.Thread(target=process_file, args=(event,))
            thread.start()

# --- Telegram Event Handlers ---

@client.on(events.NewMessage(incoming=True, func=lambda e: e.media))
async def handler(event):
    """Handles new messages with media."""
    user_id = event.sender_id
    if check_flood(user_id):
        with queue_condition:
            if check_disk_space(event.media.document.size):
                file_queue.append((event, time.time()))
                if not CREDENTIALS_FILE or TOKEN_FILE:
                    await event.reply("File added to queue.")
                    queue_condition.notify()
                else:
                    await event.reply("Not enough disk space! File not added to queue.")
    else:
        await event.reply(f"Please wait {FLOOD_TIMEOUT} seconds before sending another file.")


@client.on(events.NewMessage(pattern='/cancel'))
async def cancel_handler(event):
    """Handles /cancel command to stop download/upload."""
    try:
        file_id = int(event.message.text.split()[1])
        if file_id in processing_files:
            processing_files[file_id] = "Cancelled"
            await event.reply(f"Cancelling file with ID {file_id}")
        else:
            await event.reply("File not found in processing list.")
    except (IndexError, ValueError):
        await event.reply("Invalid command format. Usage: /cancel <file_id>")


@client.on(events.NewMessage(pattern='/credentials'))
async def credentials_handler(event):
    """Handles /credentials command to upload credentials.json."""
    if os.path.exists(CREDENTIALS_FILE):
        async with client.action(event.chat_id, 'file'):
            await event.reply_document(CREDENTIALS_FILE)
    else:
        await event.reply("Credentials file not found.")


@client.on(events.NewMessage(pattern='/token'))
async def token_handler(event):
    """Handles /token command to upload token.pickle."""
    if os.path.exists(TOKEN_FILE):
        async with client.action(event.chat_id, 'file'):
            await event.reply_document(TOKEN_FILE)
    else:
        await event.reply("Token file not found.")


@client.on(events.NewMessage(incoming=True, func=lambda e: e.file and e.file.name == CREDENTIALS_FILE))
async def update_credentials_handler(event):
    """Handles uploading a new credentials.json file."""
    global drive
    await event.respond("Updating credentials...")
    await client.download_media(event.media, file=CREDENTIALS_FILE)
    gauth = GoogleAuth()
    gauth.LoadCredentialsFile(CREDENTIALS_FILE)
    drive = GoogleDrive(gauth)
    with queue_condition:
        queue_condition.notify_all()
    await event.respond("Credentials updated successfully!")


@client.on(events.NewMessage(incoming=True, func=lambda e: e.file and e.file.name == TOKEN_FILE))
async def update_token_handler(event):
    """Handles uploading a new token.pickle file."""
    await event.respond("Updating token...")
    await client.download_media(event.media, file=TOKEN_FILE)
    await event.respond("Token updated successfully!")

# --- Start the Bot ---

worker_threads = []
for _ in range(MAX_CONCURRENT_THREADS):
    thread = threading.Thread(target=worker_thread)
    thread.daemon = True
    thread.start()
    worker_threads.append(thread)

client.run_until_disconnected()
