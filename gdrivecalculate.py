import os
import hashlib
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, CallbackContext

# Authenticate and build the Drive API service
def authenticate_drive_api():
    SCOPES = ['https://www.googleapis.com/auth/drive']
    credentials = Credentials.from_service_account_file(
        'credentials.json', scopes=SCOPES
    )
    service = build('drive', 'v3', credentials=credentials)
    return service

# List folders in Google Drive
def list_drive_folders(service):
    folders = []
    results = service.files().list(
        q="mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="nextPageToken, files(id, name)",
        pageSize=1000,
    ).execute()
    for folder in results.get('files', []):
        folders.append({'id': folder['id'], 'name': folder['name']})
    return folders

# Generate hash of a file (MD5 or SHA256)
def generate_file_hash(file_path, hash_type="md5"):
    hash_func = hashlib.md5() if hash_type.lower() == "md5" else hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            hash_func.update(byte_block)
    return hash_func.hexdigest()

# Start command handler
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Hi! Send me a file, and I’ll check if it exists in your Google Drive or allow you to upload it.")

# Handle file uploads
async def handle_file(update: Update, context: CallbackContext):
    file = update.message.document
    context.user_data['file'] = file

    # Authenticate and fetch folders
    service = authenticate_drive_api()
    folders = list_drive_folders(service)

    # Create inline keyboard for folder selection
    keyboard = [[InlineKeyboardButton(folder['name'], callback_data=folder['id'])] for folder in folders]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Prompt the user to select a folder
    await update.message.reply_text(
        "Select a folder to upload the file:",
        reply_markup=reply_markup
    )

# Handle folder selection
async def folder_selected(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()

    folder_id = query.data
    file = context.user_data.get('file')

    if not file:
        await query.edit_message_text("Error: No file found to upload.")
        return

    # Download the file from Telegram
    file_path = f"./{file.file_name}"
    await file.get_file().download_to_drive(file_path)

    # Authenticate and upload the file to the selected folder
    service = authenticate_drive_api()
    file_metadata = {
        'name': file.file_name,
        'parents': [folder_id]
    }
    media = MediaFileUpload(file_path)
    uploaded_file = service.files().create(body=file_metadata, media_body=media, fields="id").execute()

    # Cleanup local file
    os.remove(file_path)

    # Notify the user
    await query.edit_message_text(
        f"File uploaded successfully to the selected folder!\n"
        f"File ID: {uploaded_file['id']}"
    )

# Main function
def main():
    # Replace 'YOUR_TELEGRAM_BOT_TOKEN' with your Telegram bot token
    bot_token = "7505846620:AAFvv-sFybGfFILS-dRC8l7ph_0rqIhDgRM"
    app = ApplicationBuilder().token(bot_token).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(CallbackQueryHandler(folder_selected))

    # Start the bot
    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()