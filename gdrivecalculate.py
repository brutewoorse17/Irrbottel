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

# --- Google Drive Authentication ---
gauth = GoogleAuth()
# Try to load saved credentials
if os.path.exists(CREDENTIALS_FILE):
    gauth.LoadCredentialsFile(CREDENTIALS_FILE)
if gauth.credentials is None or gauth.access_token_expired:
    # If credentials are not available or expired, prompt for authentication
    gauth.LocalWebserverAuth()
    gauth.SaveCredentialsFile(CREDENTIALS_FILE)
drive = GoogleDrive(gauth)

# --- Telegram Client ---
client = TelegramClient('your_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
lock = threading.Lock()  # Lock for thread safety

# --- Global Variables ---
processing_files = {}  # Dictionary to store file IDs and their processing status
file_queue = deque(maxlen=MAX_QUEUE_SIZE)  # Queue to store files waiting to be processed
queue_condition = threading.Condition()  # Condition variable for queue synchronization
active_threads = 0  # Number of currently active threads

# --- Helper Functions ---
def check_disk_space(file_size):
    """Checks if there is enough free disk space."""
    total, used, free = shutil.disk_usage("/")
    return free - file_size > STORAGE_THRESHOLD

def process_file(event):
    """Downloads and uploads the file."""
    global active_threads
    try:
        file_id = event.media.document.id
        processing_files[file_id] = True

        client.loop.run_until_complete(event.respond("Downloading..."))
        client.loop.run_until_complete(client.download_media(event.media))

        if not processing_files[file_id]:
            return

        client.loop.run_until_complete(event.respond("Uploading..."))
        gfile = drive.CreateFile({'title': event.file.name})
        gfile.SetContentFile(event.file.name)
        gfile.Upload()

        if processing_files[file_id]:
            client.loop.run_until_complete(event.respond('File uploaded to Google Drive!'))

    except Exception as e:
        logging.error(f"Error: {e}")
        client.loop.run_until_complete(event.respond(f"An error occurred: {e}"))
    finally:
        if file_id in processing_files:
            del processing_files[file_id]
        with queue_condition:
            active_threads -= 1
            queue_condition.notify()  # Notify the queue thread that a file has been processed

def worker_thread():
    """Worker thread function to process files from the queue."""
    global active_threads
    with queue_condition:
        while True:
            while not file_queue or active_threads >= MAX_CONCURRENT_THREADS:
                queue_condition.wait()  # Wait for files or available thread slots

            event, _ = file_queue.popleft()
            active_threads += 1
            thread = threading.Thread(target=process_file, args=(event,))
            thread.start()

# --- Telegram Event Handlers ---
@client.on(events.NewMessage(incoming=True, func=lambda e: e.media))
async def handler(event):
    """Handles new messages with media."""
    with queue_condition:
        if check_disk_space(event.media.document.size):
            file_queue.append((event, time.time()))
            await event.reply("File added to queue.")
            queue_condition.notify()  # Notify the queue thread that a file has been added
        else:
            await event.reply("Not enough disk space! File not added to queue.")

@client.on(events.NewMessage(pattern='/cancel'))
async def cancel_handler(event):
    """Handles /cancel command to stop download/upload."""
    try:
        file_id = int(event.message.text.split()[1])
        if file_id in processing_files:
            processing_files[file_id] = False
            await event.reply(f"Cancelling file with ID {file_id}")
        else:
            await event.reply("File not found in processing list.")
    except (IndexError, ValueError):
        await event.reply("Invalid command format. Usage: /cancel <file_id>")

@client.on(events.NewMessage(pattern='/credentials'))
async def credentials_handler(event):
    """Handles /credentials command to upload credentials.json."""
    async with client.action(event.chat_id, 'file'):
        await event.reply_document(CREDENTIALS_FILE)

@client.on(events.NewMessage(pattern='/token'))
async def token_handler(event):
    """Handles /token command to upload token.pickle."""
    async with client.action(event.chat_id, 'file'):
        await event.reply_document(TOKEN_FILE)

@client.on(events.NewMessage(incoming=True, func=lambda e: e.file and e.file.name == CREDENTIALS_FILE))
async def update_credentials_handler(event):
    """Handles uploading a new credentials.json file."""
    await event.respond("Updating credentials...")
    await client.download_media(event.media, file=CREDENTIALS_FILE)
    await event.respond("Credentials updated successfully!")

@client.on(events.NewMessage(incoming=True, func=lambda e: e.file and e.file.name == TOKEN_FILE))
async def update_token_handler(event):
    """Handles uploading a new token.pickle file."""
    await event.respond("Updating token...")
    await client.download_media(event.media, file=TOKEN_FILE)
    await event.respond("Token updated successfully!")

# --- Start the Bot ---
worker_threads = []
for _ in range(MAX_CONCURRENT_THREADS):  # Create worker threads
    thread = threading.Thread(target=worker_thread)
    thread.daemon = True
    thread.start()
    worker_threads.append(thread)

client.run_until_disconnected()
