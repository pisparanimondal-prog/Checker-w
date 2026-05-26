"""
WhatsApp Number Checker Telegram Bot with Premium Feature - Optimized v2
Setup: pip install python-telegram-bot requests aiohttp
Run: python whatsapp_checker_bot_v2.py
"""

import logging
import requests
import json
import os
import asyncio
import aiohttp
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler
)

# ==================== CONFIGURATION ====================
TELEGRAM_BOT_TOKEN = "7469743304:AAHVlbMHqQ-LEFjbkLpI4d-Pgy1v8WUoHeM"
MAYTAPI_PRODUCT_ID = "702aaddd-cfbd-4393-95e5-f59ad27c1dda"
MAYTAPI_PHONE_ID = "138218"
MAYTAPI_TOKEN = "1789a452-a8e6-492f-8fba-7c4e5a436b8e"
ADMIN_IDS = [5692411527]

# Payment settings
PREMIUM_PRICE = 1.0  # USDT
PREMIUM_DURATION_DAYS = 3
PAYMENT_METHOD = "USDT/TRC20"
ADMIN_PAYMENT_ADDRESS = "TCM12eGmKUtAv6neKM2DJJdQv7m9qnGf3C"

# Performance settings
CONCURRENT_REQUESTS = 10  # Number of concurrent API calls
BATCH_SIZE = 5  # Update progress every N numbers (lower = more frequent updates)
MAX_MESSAGE_LENGTH = 3800  # Telegram message limit (leaving buffer for formatting)
# ==================== END CONFIGURATION ====================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
WAITING_PRODUCT_ID = 1
WAITING_PHONE_ID = 2
WAITING_API_TOKEN = 3
WAITING_PAYMENT_PROOF = 4

# Data storage files - Use relative path for Android/Pydroid3 compatibility
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data")
PREMIUM_FILE = os.path.join(DATA_DIR, "premium_users.json")
CREDENTIALS_FILE = os.path.join(DATA_DIR, "user_credentials.json")

# Ensure data directory exists
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except PermissionError:
    DATA_DIR = os.path.join(os.getcwd(), "bot_data")
    PREMIUM_FILE = os.path.join(DATA_DIR, "premium_users.json")
    CREDENTIALS_FILE = os.path.join(DATA_DIR, "user_credentials.json")
    os.makedirs(DATA_DIR, exist_ok=True)

# ==================== DATA PERSISTENCE ====================

def load_premium_users():
    if os.path.exists(PREMIUM_FILE):
        with open(PREMIUM_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_premium_users(premium_users):
    with open(PREMIUM_FILE, 'w') as f:
        json.dump(premium_users, f, indent=2)

def load_user_credentials():
    if os.path.exists(CREDENTIALS_FILE):
        with open(CREDENTIALS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_user_credentials(credentials):
    with open(CREDENTIALS_FILE, 'w') as f:
        json.dump(credentials, f, indent=2)

# Load data
premium_users = load_premium_users()
user_credentials = load_user_credentials()
checking_mode_users = set()
pending_payments = {}

# ==================== HELPER FUNCTIONS ====================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def credentials_set() -> bool:
    return all([
        TELEGRAM_BOT_TOKEN and TELEGRAM_BOT_TOKEN != "YOUR_TELEGRAM_BOT_TOKEN_HERE",
        MAYTAPI_PRODUCT_ID and MAYTAPI_PRODUCT_ID != "YOUR_PRODUCT_ID_HERE",
        MAYTAPI_PHONE_ID and MAYTAPI_PHONE_ID != "YOUR_PHONE_ID_HERE",
        MAYTAPI_TOKEN and MAYTAPI_TOKEN != "YOUR_API_TOKEN_HERE"
    ])

def is_premium_active(user_id: int) -> bool:
    if str(user_id) not in premium_users:
        return False
    expires_str = premium_users[str(user_id)].get("expires")
    if not expires_str:
        return False
    expires = datetime.fromisoformat(expires_str)
    return datetime.now() < expires

def get_premium_expiry(user_id: int) -> str:
    if str(user_id) in premium_users:
        expires_str = premium_users[str(user_id)].get("expires")
        if expires_str:
            expires = datetime.fromisoformat(expires_str)
            return expires.strftime("%Y-%m-%d %H:%M")
    return ""

def add_premium(user_id: int, days: int = PREMIUM_DURATION_DAYS):
    now = datetime.now()
    expires = now + timedelta(days=days)
    premium_users[str(user_id)] = {
        "purchased_at": now.isoformat(),
        "expires": expires.isoformat(),
        "days": days
    }
    save_premium_users(premium_users)

def remove_premium(user_id: int):
    if str(user_id) in premium_users:
        del premium_users[str(user_id)]
        save_premium_users(premium_users)

def user_has_credentials(user_id: int) -> bool:
    if is_admin(user_id):
        return credentials_set()
    if is_premium_active(user_id):
        return credentials_set()
    if str(user_id) not in user_credentials:
        return False
    creds = user_credentials[str(user_id)]
    return all([
        creds.get('product_id'),
        creds.get('phone_id'),
        creds.get('token')
    ])

def get_user_creds(user_id: int) -> dict:
    if is_admin(user_id) or is_premium_active(user_id):
        return {
            "product_id": MAYTAPI_PRODUCT_ID,
            "phone_id": MAYTAPI_PHONE_ID,
            "token": MAYTAPI_TOKEN
        }
    return user_credentials.get(str(user_id), {})

def clean_number(number: str) -> str:
    cleaned = number.strip()
    for char in ['\u200e', '\u200f', '\u202a', '\u202b', '\u202c', '\ufeff']:
        cleaned = cleaned.replace(char, '')
    cleaned = cleaned.replace('+', '').replace(' ', '').replace('-', '').replace(',', '')
    return cleaned

def parse_numbers(text: str) -> list:
    lines = text.split('\n')
    numbers = []
    for line in lines:
        parts = line.replace(',', ' ').split()
        for part in parts:
            cleaned = clean_number(part)
            if cleaned.isdigit() and len(cleaned) >= 8:
                numbers.append(cleaned)
    return numbers

# Progress bar generator
def get_progress_bar(current: int, total: int, length: int = 20) -> str:
    filled = int(length * current / total)
    bar = '█' * filled + '░' * (length - filled)
    percentage = int(100 * current / total)
    return f"[{bar}] {percentage}%"

# ==================== ASYNC API CHECKING ====================

async def check_single_number_async(session: aiohttp.ClientSession, phone_number: str, creds: dict) -> dict:
    """Check a phone number using async HTTP request."""
    if not creds or not all([creds.get('product_id'), creds.get('phone_id'), creds.get('token')]):
        return {"success": False, "error": "Credentials not configured", "number": phone_number}
    
    url = f"https://api.maytapi.com/api/{creds['product_id']}/{creds['phone_id']}/checkNumberStatus"
    headers = {"x-maytapi-key": creds['token'], "Content-Type": "application/json"}
    params = {"number": f"{phone_number}@c.us"}
    
    try:
        async with session.get(url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=30)) as response:
            if response.status == 200:
                data = await response.json()
                # MaytAPI returns: {"success": true, "result": {"status": 200, ...}}
                # The actual WhatsApp data is inside "result" key
                return {"success": True, "data": data, "number": phone_number}
            else:
                error_text = await response.text()
                return {"success": False, "error": f"HTTP {response.status}: {error_text}", "number": phone_number}
    except Exception as e:
        return {"success": False, "error": str(e), "number": phone_number}

async def check_numbers_with_progress(numbers: list, user_id: int, context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    """Check numbers with live progress updates."""
    creds = get_user_creds(user_id)
    total = len(numbers)
    results = []
    
    # Create progress message
    progress_text = (
        f"<b>🚀 Checking {total} Numbers</b>\n\n"
        f"{get_progress_bar(0, total)}\n"
        f"<b>Progress:</b> 0/{total}\n\n"
        f"⏳ <i>Starting...</i>"
    )
    
    try:
        await context.bot.edit_message_text(
            progress_text,
            chat_id=chat_id,
            message_id=message_id,
            parse_mode='HTML'
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            pass  # Ignore this harmless error
        else:
            logger.error(f"Failed to update progress: {e}")
    except Exception as e:
        logger.error(f"Failed to update progress: {e}")
    
    # Process numbers in batches with concurrency
    async with aiohttp.ClientSession() as session:
        for i in range(0, total, CONCURRENT_REQUESTS):
            batch = numbers[i:i + CONCURRENT_REQUESTS]
            
            # Create tasks for concurrent execution
            tasks = [check_single_number_async(session, num, creds) for num in batch]
            batch_results = await asyncio.gather(*tasks)
            results.extend(batch_results)
            
            # Update progress after every batch (more frequent updates)
            current_count = len(results)
            # Always update progress after each batch for live feel
            batch_num = (i // CONCURRENT_REQUESTS) + 1
            total_batches = (total + CONCURRENT_REQUESTS - 1) // CONCURRENT_REQUESTS
            # Update every batch or every BATCH_SIZE numbers, whichever is more frequent
            should_update = (batch_num <= 5) or (current_count % BATCH_SIZE == 0) or (current_count == total)
            if should_update:
                # Count results so far - MaytAPI returns: {"success": true, "result": {"status": 200, ...}}
                def has_whatsapp(r):
                    if not r.get("success"):
                        return False
                    # The API response structure is: {"success": true, "result": {"status": 200, ...}}
                    api_data = r.get("data", {})
                    result_data = api_data.get("result", {}) if isinstance(api_data, dict) else {}
                    status = result_data.get("status") if isinstance(result_data, dict) else None
                    return status == 200
                
                whatsapp_count = sum(1 for r in results if has_whatsapp(r))
                no_whatsapp_count = sum(1 for r in results if r.get("success") and not has_whatsapp(r))
                error_count = sum(1 for r in results if not r.get("success"))
                
                # Calculate speed/ETA
                percent = int(100 * current_count / total)
                
                progress_text = (
                    f"<b>🚀 Checking {total} Numbers</b>\n\n"
                    f"{get_progress_bar(current_count, total)}\n"
                    f"<b>Progress:</b> {current_count}/{total} ({percent}%)\n"
                    f"<b>Batch:</b> {batch_num}/{total_batches}\n\n"
                    f"<b>📊 Live Results:</b>\n"
                    f"✅ WhatsApp: {whatsapp_count}\n"
                    f"❌ No WhatsApp: {no_whatsapp_count}\n"
                    f"⚠️ Errors: {error_count}\n\n"
                    f"⏳ <i>Processing batch {batch_num}...</i>"
                )
                
                try:
                    await context.bot.edit_message_text(
                        progress_text,
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode='HTML'
                    )
                except BadRequest as e:
                    if "Message is not modified" in str(e):
                        pass  # Ignore this harmless error
                    else:
                        logger.error(f"Failed to update progress: {e}")
                except Exception as e:
                    logger.error(f"Failed to update progress: {e}")
            
            # Small delay to avoid rate limiting
            await asyncio.sleep(0.1)
    
    return results

# ==================== MESSAGE TEMPLATES ====================

def get_welcome_message(name: str, is_admin_user: bool, user_id: int) -> str:
    if is_admin_user:
        return (
            f"👋 Hello, <b>{name}</b>!\n\n"
            f"<b>Welcome to WhatsApp Number Checker Bot!</b>\n\n"
            f"I can check if phone numbers have WhatsApp accounts.\n\n"
            f"<b>How to use:</b>\n"
            f"1. Use /check to start checking\n"
            f"2. Send phone numbers\n"
            f"3. I'll check them and show results\n\n"
            f"<b>Admin Commands:</b>\n"
            f"• /setcreds - Update your API credentials\n"
            f"• /status - View bot status\n"
            f"• /premiumusers - View premium users"
        )
    
    has_own_creds = str(user_id) in user_credentials and all([
        user_credentials[str(user_id)].get('product_id'),
        user_credentials[str(user_id)].get('phone_id'),
        user_credentials[str(user_id)].get('token')
    ])
    is_premium = is_premium_active(user_id)
    
    if is_premium:
        expiry = get_premium_expiry(user_id)
        return (
            f"👋 Hello, <b>{name}</b>!\n\n"
            f"<b>Welcome to WhatsApp Number Checker Bot!</b>\n\n"
            f"✅ <b>Premium Active</b> until {expiry}\n\n"
            f"<b>How to use:</b>\n"
            f"1. Use /check to start checking\n"
            f"2. Send phone numbers\n"
            f"3. I'll check them and show results\n\n"
            f"<b>Number formats:</b>\n"
            f"• 79959700390\n"
            f"• 79959700390, 79959700411"
        )
    elif has_own_creds:
        return (
            f"👋 Hello, <b>{name}</b>!\n\n"
            f"<b>Welcome to WhatsApp Number Checker Bot!</b>\n\n"
            f"✅ Your API is configured\n\n"
            f"<b>How to use:</b>\n"
            f"1. Use /check to start checking\n"
            f"2. Send phone numbers\n"
            f"3. I'll check them and show results\n\n"
            f"<b>Want Premium?</b> Use /premium"
        )
    else:
        return (
            f"👋 Hello, <b>{name}</b>!\n\n"
            f"<b>Welcome to WhatsApp Number Checker Bot!</b>\n\n"
            f"I can check if phone numbers have WhatsApp accounts.\n\n"
            f"<b>Choose an option:</b>"
        )

def get_start_keyboard(user_id: int, is_admin_user: bool) -> InlineKeyboardMarkup:
    if is_admin_user:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 Start Checking", callback_data="check")],
            [InlineKeyboardButton("⚙️ My Status", callback_data="status")]
        ])
    
    has_own_creds = str(user_id) in user_credentials and all([
        user_credentials[str(user_id)].get('product_id'),
        user_credentials[str(user_id)].get('phone_id'),
        user_credentials[str(user_id)].get('token')
    ])
    is_premium = is_premium_active(user_id)
    
    if is_premium or has_own_creds:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 Start Checking", callback_data="check")],
            [InlineKeyboardButton("⚙️ My Status", callback_data="status")]
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔧 Setup My API", callback_data="setup")],
            [InlineKeyboardButton("⭐ Buy Premium", callback_data="buy_premium")]
        ])

HELP_MESSAGE = (
    "<b>📖 Help</b>\n\n"
    "<b>Available Commands:</b>\n"
    "• /start - Welcome message\n"
    "• /setup - Configure your own API\n"
    "• /premium - Buy premium access\n"
    "• /check - Start checking numbers\n"
    "• /stop - Stop checking mode\n"
    "• /status - Check your account status\n"
    "• /help - Show this help\n\n"
    "<b>How to check numbers:</b>\n"
    "1. Type /check to activate\n"
    "2. Send your phone numbers\n"
    "3. Get results instantly!\n\n"
    "<b>Accepted formats:</b>\n"
    "• 79959700390\n"
    "• +79959700390\n"
    "• 79959700390, 79959700411"
)

SETUP_WELCOME = (
    "<b>🔧 Setup Your API Credentials</b>\n\n"
    "Please enter your MaytAPI credentials:\n\n"
    "1️⃣ <b>Product ID</b>\n"
    "2️⃣ <b>Phone ID</b>\n"
    "3️⃣ <b>API Token</b>\n\n"
    "ℹ️ Find these in your MaytAPI dashboard.\n\n"
    "Please enter your <b>Product ID</b>:"
)

SETUP_COMPLETE = (
    "<b>✅ Setup Complete!</b>\n\n"
    "Your API credentials have been saved.\n\n"
    "🎯 <b>You can now check numbers!</b>\n"
    "Type /check to start."
)

SETUP_CANCELLED = (
    "<b>❌ Setup Cancelled</b>\n\n"
    "You can start again anytime by typing /setup"
)

PREMIUM_INFO = (
    "<b>⭐ Premium Access</b>\n\n"
    f"<b>Price:</b> ${PREMIUM_PRICE} for {PREMIUM_DURATION_DAYS} days\n\n"
    "<b>What you get:</b>\n"
    "✅ Use our premium API (no setup needed)\n"
    "✅ Fast and reliable checking\n"
    "✅ No need to configure your own API\n\n"
    "<b>Payment Method:</b> {method}\n"
    "<b>Send payment to:</b> <code>{address}</code>\n\n"
    "Click the button below when you've sent the payment."
)

PREMIUM_PENDING = (
    "<b>⏳ Payment Verification Pending</b>\n\n"
    "Your payment is being verified by the admin.\n"
    "You'll be notified once your premium is activated.\n\n"
    "Thank you for your patience!"
)

PREMIUM_ACTIVATED = (
    "<b>🎉 Premium Activated!</b>\n\n"
    "Your premium access is now active!\n"
    "You can now use /check to verify numbers.\n\n"
    "Enjoy!"
)

CHECKING_MODE_ACTIVE = (
    "<b>✅ Checking Mode Activated!</b>\n\n"
    "Send me phone numbers and I'll check them.\n\n"
    "<b>You can send:</b>\n"
    "• Single or multiple numbers\n"
    "• Any format (with/without +)\n"
    "• Separated by space, comma, or new lines\n\n"
    "💬 <b>Waiting for your numbers...</b>"
)

CHECKING_MODE_STOPPED = (
    "<b>🛑 Checking Mode Deactivated</b>\n\n"
    "You've exited checking mode.\n\n"
    "🎯 Type /check to start again!"
)

NOT_IN_CHECKING_MODE = (
    "<b>🔍 Not in Checking Mode</b>\n\n"
    "To check phone numbers:\n"
    "1️⃣ Type /check to activate\n"
    "2️⃣ Send your numbers\n"
    "3️⃣ Get results instantly!\n\n"
    "🎯 <b>Type /check now!</b>"
)

SETUP_REQUIRED = (
    "<b>🔐 Setup Required</b>\n\n"
    "You need to configure your API credentials first.\n\n"
    "📋 <b>Options:</b>\n"
    "• Setup your own API: /setup\n"
    "• Buy premium access: /premium\n\n"
    "🎯 <b>Choose an option above!</b>"
)

NO_NUMBERS_FOUND = (
    "<b>🤔 No Numbers Found</b>\n\n"
    "I couldn't find any valid phone numbers.\n\n"
    "🎯 Please send numbers like: <code>79959700390</code>"
)

ADMIN_ONLY = (
    "<b>🔒 Admin Only</b>\n\n"
    "❌ This command is only available to admins."
)

# ==================== COMMAND HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    is_admin_user = is_admin(user_id)
    
    welcome = get_welcome_message(name, is_admin_user, user_id)
    keyboard = get_start_keyboard(user_id, is_admin_user)
    
    await update.message.reply_text(welcome, reply_markup=keyboard, parse_mode='HTML')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_MESSAGE, parse_mode='HTML')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    is_admin_user = is_admin(user_id)
    
    status = "<b>📊 Your Account Status</b>\n\n"
    
    if is_admin_user:
        status += "<b>Role:</b> Admin\n"
        status += "<b>API:</b> Using default credentials\n"
    elif is_premium_active(user_id):
        expiry = get_premium_expiry(user_id)
        status += "<b>Role:</b> Premium User\n"
        status += f"<b>Premium expires:</b> {expiry}\n"
        status += "<b>API:</b> Using premium API\n"
    elif str(user_id) in user_credentials and all([
        user_credentials[str(user_id)].get('product_id'),
        user_credentials[str(user_id)].get('phone_id'),
        user_credentials[str(user_id)].get('token')
    ]):
        status += "<b>Role:</b> Regular User\n"
        status += "<b>API:</b> Using your own credentials\n"
    else:
        status += "<b>Role:</b> New User\n"
        status += "<b>API:</b> Not configured\n\n"
        status += "🎯 Use /setup or /premium to get started!"
    
    await update.message.reply_text(status, parse_mode='HTML')

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    if user_id in checking_mode_users:
        checking_mode_users.discard(user_id)
        await update.message.reply_text(CHECKING_MODE_STOPPED, parse_mode='HTML')
    else:
        await update.message.reply_text(NOT_IN_CHECKING_MODE, parse_mode='HTML')

async def premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    if is_admin(user_id):
        await update.message.reply_text("<b>ℹ️ You're an admin. You already have full access!</b>", parse_mode='HTML')
        return
    
    if is_premium_active(user_id):
        expiry = get_premium_expiry(user_id)
        await update.message.reply_text(
            f"<b>⭐ You already have Premium!</b>\n\n"
            f"Your premium is active until: <b>{expiry}</b>\n\n"
            f"You can use /check to verify numbers.",
            parse_mode='HTML'
        )
        return
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 I've Sent Payment", callback_data="payment_sent")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_payment")]
    ])
    
    premium_msg = PREMIUM_INFO.format(method=PAYMENT_METHOD, address=ADMIN_PAYMENT_ADDRESS)
    await update.message.reply_text(premium_msg, reply_markup=keyboard, parse_mode='HTML')

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    if not user_has_credentials(user_id):
        await update.message.reply_text(SETUP_REQUIRED, parse_mode='HTML')
        return
    
    # If numbers provided with command
    if context.args:
        numbers_text = " ".join(context.args)
        numbers = parse_numbers(numbers_text)
        if numbers:
            checking_mode_users.add(user_id)
            await process_numbers(update, context, numbers)
            return
        else:
            await update.message.reply_text(NO_NUMBERS_FOUND, parse_mode='HTML')
            return
    
    # No numbers provided - activate checking mode
    checking_mode_users.add(user_id)
    await update.message.reply_text(CHECKING_MODE_ACTIVE, parse_mode='HTML')

async def process_numbers(update: Update, context: ContextTypes.DEFAULT_TYPE, numbers: list) -> None:
    if not numbers:
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    total = len(numbers)
    
    # Send initial progress message
    progress_msg = await update.message.reply_text(
        f"<b>🚀 Checking {total} Numbers</b>\n\n"
        f"{get_progress_bar(0, total)}\n"
        f"<b>Progress:</b> 0/{total}\n\n"
        f"⏳ <i>Starting...</i>",
        parse_mode='HTML'
    )
    
    # Check all numbers with live progress
    results = await check_numbers_with_progress(
        numbers, user_id, context, chat_id, progress_msg.message_id
    )
    
    # Build final results
    whatsapp_numbers = []
    no_whatsapp_numbers = []
    error_numbers = []
    
    for result in results:
        number = result.get("number", "")
        if result.get("success"):
            # MaytAPI response: {"success": true, "result": {"status": 200, "isBusiness": false, ...}}
            api_data = result.get("data", {})
            result_data = api_data.get("result", {}) if isinstance(api_data, dict) else {}
            status = result_data.get("status") if isinstance(result_data, dict) else None
            
            if status == 200:
                # Valid WhatsApp number found
                is_business = result_data.get("isBusiness", False)
                business_type = "Business" if is_business else "Personal"
                whatsapp_numbers.append((number, business_type))
            else:
                # No WhatsApp or invalid number
                no_whatsapp_numbers.append(number)
        else:
            error_numbers.append((number, result.get("error", "Unknown")))
    
    # Build final response - Show ALL numbers, split into multiple messages if needed
    messages = []
    current_msg = f"<b>✅ Check Complete! {total} Numbers Processed</b>\n\n"
    current_msg += f"<b>📊 Summary:</b>\n"
    current_msg += f"✅ WhatsApp: {len(whatsapp_numbers)}\n"
    current_msg += f"❌ No WhatsApp: {len(no_whatsapp_numbers)}\n"
    current_msg += f"⚠️ Errors: {len(error_numbers)}\n\n"
    
    # Show ALL WhatsApp numbers
    if whatsapp_numbers:
        section_header = f"<b>📱 Numbers with WhatsApp ({len(whatsapp_numbers)}):</b>\n"
        if len(current_msg) + len(section_header) > MAX_MESSAGE_LENGTH:
            messages.append(current_msg)
            current_msg = section_header
        else:
            current_msg += section_header
        
        for num, btype in whatsapp_numbers:
            line = f"✅ <code>{num}</code> | {btype}\n"
            if len(current_msg) + len(line) > MAX_MESSAGE_LENGTH:
                messages.append(current_msg)
                current_msg = line
            else:
                current_msg += line
        current_msg += "\n"
    
    # Show ALL numbers without WhatsApp
    if no_whatsapp_numbers:
        section_header = f"<b>❌ No WhatsApp ({len(no_whatsapp_numbers)}):</b>\n"
        if len(current_msg) + len(section_header) > MAX_MESSAGE_LENGTH:
            messages.append(current_msg)
            current_msg = section_header
        else:
            current_msg += section_header
        
        for num in no_whatsapp_numbers:
            line = f"❌ <code>{num}</code>\n"
            if len(current_msg) + len(line) > MAX_MESSAGE_LENGTH:
                messages.append(current_msg)
                current_msg = line
            else:
                current_msg += line
        current_msg += "\n"
    
    # Show ALL errors if any
    if error_numbers:
        section_header = f"<b>⚠️ Errors ({len(error_numbers)}):</b>\n"
        if len(current_msg) + len(section_header) > MAX_MESSAGE_LENGTH:
            messages.append(current_msg)
            current_msg = section_header
        else:
            current_msg += section_header
        
        for num, error in error_numbers:
            line = f"⚠️ <code>{num}</code> - {error}\n"
            if len(current_msg) + len(line) > MAX_MESSAGE_LENGTH:
                messages.append(current_msg)
                current_msg = line
            else:
                current_msg += line
    
    # Add footer to last message
    footer = "\n🎯 <b>Use /check to check more numbers!</b>"
    if len(current_msg) + len(footer) > MAX_MESSAGE_LENGTH:
        messages.append(current_msg)
        current_msg = footer
    else:
        current_msg += footer
    
    messages.append(current_msg)
    
    # Send all messages - first one edits the progress message, rest are new messages
    try:
        # Update the progress message with first part
        await context.bot.edit_message_text(
            messages[0],
            chat_id=chat_id,
            message_id=progress_msg.message_id,
            parse_mode='HTML'
        )
        
        # Send remaining parts as new messages
        for msg_part in messages[1:]:
            await context.bot.send_message(
                chat_id=chat_id,
                text=msg_part,
                parse_mode='HTML'
            )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            pass  # Message already shows correct content
        else:
            logger.error(f"Failed to send final results: {e}")
    except Exception as e:
        logger.error(f"Failed to send final results: {e}")
        # Fallback: send as new message
        for msg_part in messages:
            await update.message.reply_text(msg_part, parse_mode='HTML')
    
    # Remove user from checking mode
    checking_mode_users.discard(user_id)

async def handle_numbers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    if not user_has_credentials(user_id):
        await update.message.reply_text(SETUP_REQUIRED, parse_mode='HTML')
        return
    
    if user_id not in checking_mode_users:
        await update.message.reply_text(NOT_IN_CHECKING_MODE, parse_mode='HTML')
        return
    
    text = update.message.text
    numbers = parse_numbers(text)
    
    if numbers:
        await process_numbers(update, context, numbers)
    else:
        await update.message.reply_text(NO_NUMBERS_FOUND, parse_mode='HTML')

# ==================== CALLBACK HANDLERS ====================

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button callbacks - FIXED to not use update.message"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    
    if data == "setup":
        await query.edit_message_text(SETUP_WELCOME, parse_mode='HTML')
        context.user_data['setup_state'] = WAITING_PRODUCT_ID
        return WAITING_PRODUCT_ID
    
    elif data == "buy_premium":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 I've Sent Payment", callback_data="payment_sent")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_payment")]
        ])
        premium_msg = PREMIUM_INFO.format(method=PAYMENT_METHOD, address=ADMIN_PAYMENT_ADDRESS)
        await query.edit_message_text(premium_msg, reply_markup=keyboard, parse_mode='HTML')
    
    elif data == "payment_sent":
        pending_payments[user_id] = {
            "message_id": query.message.message_id,
            "started_at": datetime.now().isoformat()
        }
        await query.edit_message_text(PREMIUM_PENDING, parse_mode='HTML')
        
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    admin_id,
                    f"<b>💳 New Premium Payment</b>\n\n"
                    f"User ID: <code>{user_id}</code>\n"
                    f"Username: @{update.effective_user.username or 'N/A'}\n"
                    f"Name: {update.effective_user.first_name}\n\n"
                    f"Use /approve {user_id} to activate premium\n"
                    f"Use /reject {user_id} to reject",
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
    
    elif data == "cancel_payment":
        if user_id in pending_payments:
            del pending_payments[user_id]
        await query.edit_message_text("<b>❌ Payment Cancelled</b>\n\nType /premium to try again.", parse_mode='HTML')
    
    elif data == "check":
        # FIXED: Handle check button directly without calling check_command
        if not user_has_credentials(user_id):
            await query.edit_message_text(SETUP_REQUIRED, parse_mode='HTML')
            return
        
        checking_mode_users.add(user_id)
        await query.edit_message_text(CHECKING_MODE_ACTIVE, parse_mode='HTML')
    
    elif data == "status":
        # FIXED: Handle status button directly without calling status_command
        is_admin_user = is_admin(user_id)
        
        status = "<b>📊 Your Account Status</b>\n\n"
        
        if is_admin_user:
            status += "<b>Role:</b> Admin\n"
            status += "<b>API:</b> Using default credentials\n"
        elif is_premium_active(user_id):
            expiry = get_premium_expiry(user_id)
            status += "<b>Role:</b> Premium User\n"
            status += f"<b>Premium expires:</b> {expiry}\n"
            status += "<b>API:</b> Using premium API\n"
        elif str(user_id) in user_credentials and all([
            user_credentials[str(user_id)].get('product_id'),
            user_credentials[str(user_id)].get('phone_id'),
            user_credentials[str(user_id)].get('token')
        ]):
            status += "<b>Role:</b> Regular User\n"
            status += "<b>API:</b> Using your own credentials\n"
        else:
            status += "<b>Role:</b> New User\n"
            status += "<b>API:</b> Not configured\n\n"
            status += "🎯 Use /setup or /premium to get started!"
        
        await query.edit_message_text(status, parse_mode='HTML')

# ==================== SETUP COMMAND ====================

async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    
    if is_premium_active(user_id):
        await update.message.reply_text(
            "<b>ℹ️ You have Premium active!</b>\n\n"
            "You don't need to setup your own API.\n"
            "Use /check to start checking numbers.",
            parse_mode='HTML'
        )
        return ConversationHandler.END
    
    await update.message.reply_text(SETUP_WELCOME, parse_mode='HTML')
    return WAITING_PRODUCT_ID

async def setup_receive_product_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if str(user_id) not in user_credentials:
        user_credentials[str(user_id)] = {}
    user_credentials[str(user_id)]['product_id'] = text
    
    await update.message.reply_text(
        "<b>✅ Product ID Saved!</b>\n\n"
        "Now please enter your <b>Phone ID</b>:",
        parse_mode='HTML'
    )
    return WAITING_PHONE_ID

async def setup_receive_phone_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    user_credentials[str(user_id)]['phone_id'] = text
    
    await update.message.reply_text(
        "<b>✅ Phone ID Saved!</b>\n\n"
        "Now please enter your <b>API Token</b>:",
        parse_mode='HTML'
    )
    return WAITING_API_TOKEN

async def setup_receive_api_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    user_credentials[str(user_id)]['token'] = text
    save_user_credentials(user_credentials)
    
    await update.message.reply_text(SETUP_COMPLETE, parse_mode='HTML')
    return ConversationHandler.END

async def setup_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(SETUP_CANCELLED, parse_mode='HTML')
    return ConversationHandler.END

# ==================== ADMIN COMMANDS ====================

async def admin_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(ADMIN_ONLY, parse_mode='HTML')
        return
    
    active_premium = sum(1 for uid, data in premium_users.items() 
                        if datetime.now() < datetime.fromisoformat(data.get("expires", "2000-01-01")))
    
    status = (
        "<b>📊 Bot Status</b>\n\n"
        "<b>Default Credentials:</b>\n"
        f"• Bot Token: {'✅ Set' if TELEGRAM_BOT_TOKEN and TELEGRAM_BOT_TOKEN != 'YOUR_TELEGRAM_BOT_TOKEN_HERE' else '❌ Not Set'}\n"
        f"• Product ID: {'✅ Set' if MAYTAPI_PRODUCT_ID and MAYTAPI_PRODUCT_ID != 'YOUR_PRODUCT_ID_HERE' else '❌ Not Set'}\n"
        f"• Phone ID: {'✅ Set' if MAYTAPI_PHONE_ID and MAYTAPI_PHONE_ID != 'YOUR_PHONE_ID_HERE' else '❌ Not Set'}\n"
        f"• API Token: {'✅ Set' if MAYTAPI_TOKEN and MAYTAPI_TOKEN != 'YOUR_API_TOKEN_HERE' else '❌ Not Set'}\n\n"
        f"<b>Active Premium Users:</b> {active_premium}\n"
        f"<b>Total Premium Users:</b> {len(premium_users)}\n"
        f"<b>Users with Own API:</b> {len(user_credentials)}\n"
        f"<b>Pending Payments:</b> {len(pending_payments)}"
    )
    
    await update.message.reply_text(status, parse_mode='HTML')

async def approve_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(ADMIN_ONLY, parse_mode='HTML')
        return
    
    if not context.args:
        await update.message.reply_text("<b>Usage:</b> /approve &lt;user_id&gt; [days]", parse_mode='HTML')
        return
    
    try:
        target_user_id = int(context.args[0])
        days = int(context.args[1]) if len(context.args) > 1 else PREMIUM_DURATION_DAYS
    except ValueError:
        await update.message.reply_text("<b>❌ Invalid user ID or days</b>", parse_mode='HTML')
        return
    
    add_premium(target_user_id, days)
    
    await update.message.reply_text(
        f"<b>✅ Premium Activated!</b>\n\n"
        f"User ID: <code>{target_user_id}</code>\n"
        f"Duration: {days} days\n"
        f"Expires: {get_premium_expiry(target_user_id)}",
        parse_mode='HTML'
    )
    
    try:
        await context.bot.send_message(
            target_user_id,
            PREMIUM_ACTIVATED,
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Failed to notify user {target_user_id}: {e}")

async def reject_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(ADMIN_ONLY, parse_mode='HTML')
        return
    
    if not context.args:
        await update.message.reply_text("<b>Usage:</b> /reject &lt;user_id&gt;", parse_mode='HTML')
        return
    
    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("<b>❌ Invalid user ID</b>", parse_mode='HTML')
        return
    
    if target_user_id in pending_payments:
        del pending_payments[target_user_id]
    
    await update.message.reply_text(
        f"<b>❌ Payment Rejected</b>\n\n"
        f"User ID: <code>{target_user_id}</code>",
        parse_mode='HTML'
    )
    
    try:
        await context.bot.send_message(
            target_user_id,
            "<b>❌ Payment Rejected</b>\n\n"
            "Your premium payment was not approved.\n"
            "Please contact admin or try again with /premium",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Failed to notify user {target_user_id}: {e}")

async def premium_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(ADMIN_ONLY, parse_mode='HTML')
        return
    
    if not premium_users:
        await update.message.reply_text("<b>No premium users yet.</b>", parse_mode='HTML')
        return
    
    response = "<b>⭐ Premium Users</b>\n\n"
    
    for uid, data in premium_users.items():
        expires = datetime.fromisoformat(data.get("expires", "2000-01-01"))
        is_active = datetime.now() < expires
        status = "✅ Active" if is_active else "❌ Expired"
        response += f"User: <code>{uid}</code> | {status} | Expires: {expires.strftime('%Y-%m-%d')}\n"
    
    await update.message.reply_text(response, parse_mode='HTML')

async def remove_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(ADMIN_ONLY, parse_mode='HTML')
        return
    
    if not context.args:
        await update.message.reply_text("<b>Usage:</b> /removepremium &lt;user_id&gt;", parse_mode='HTML')
        return
    
    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("<b>❌ Invalid user ID</b>", parse_mode='HTML')
        return
    
    remove_premium(target_user_id)
    
    await update.message.reply_text(
        f"<b>✅ Premium Removed</b>\n\n"
        f"User ID: <code>{target_user_id}</code>",
        parse_mode='HTML'
    )

async def test_api_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Test API with a single number and show raw response (admin only)"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(ADMIN_ONLY, parse_mode='HTML')
        return
    
    if not context.args:
        await update.message.reply_text(
            "<b>Usage:</b> /testapi &lt;phone_number&gt;\n\n"
            "Example: <code>/testapi 917908195922</code>",
            parse_mode='HTML'
        )
        return
    
    phone_number = clean_number(context.args[0])
    creds = get_user_creds(user_id)
    
    await update.message.reply_text(
        f"<b>🔍 Testing API...</b>\n\n"
        f"Number: <code>{phone_number}</code>\n"
        f"Checking with MaytAPI...",
        parse_mode='HTML'
    )
    
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            url = f"https://api.maytapi.com/api/{creds['product_id']}/{creds['phone_id']}/checkNumberStatus"
            headers = {"x-maytapi-key": creds['token'], "Content-Type": "application/json"}
            params = {"number": f"{phone_number}@c.us"}
            
            async with session.get(url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=30)) as response:
                status_code = response.status
                raw_response = await response.text()
                
                try:
                    json_data = json.loads(raw_response)
                    formatted_json = json.dumps(json_data, indent=2)
                except:
                    formatted_json = raw_response
                
                # Determine result - API returns: {"success": true, "result": {"status": 200, ...}}
                result_data = json_data.get("result", {}) if isinstance(json_data, dict) else {}
                api_status = result_data.get("status") if isinstance(result_data, dict) else None
                
                if api_status == 200:
                    result_emoji = "✅ Has WhatsApp"
                    is_business = result_data.get("isBusiness", False)
                    biz_type = "Business" if is_business else "Personal"
                    result_emoji += f" ({biz_type})"
                elif api_status is not None:
                    result_emoji = "❌ No WhatsApp"
                else:
                    result_emoji = "⚠️ Unknown response"
                
                response_text = (
                    f"<b>🔍 API Test Result</b>\n\n"
                    f"<b>Number:</b> <code>{phone_number}</code>\n"
                    f"<b>HTTP Status:</b> {status_code}\n"
                    f"<b>Result:</b> {result_emoji}\n\n"
                    f"<b>Raw API Response:</b>\n"
                    f"<pre>{formatted_json[:3500]}</pre>"
                )
                
                await update.message.reply_text(response_text, parse_mode='HTML')
                
    except Exception as e:
        await update.message.reply_text(
            f"<b>❌ Error testing API:</b>\n<code>{str(e)}</code>",
            parse_mode='HTML'
        )

async def setcreds_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("<b>🔒 Permission Denied</b>\n\n❌ You don't have permission to use this command.", parse_mode='HTML')
        return ConversationHandler.END
    
    await update.message.reply_text(
        "<b>🔧 Update Admin Credentials</b>\n\n"
        "Enter new <b>Product ID</b>:",
        parse_mode='HTML'
    )
    return WAITING_PRODUCT_ID

async def receive_product_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    global MAYTAPI_PRODUCT_ID
    MAYTAPI_PRODUCT_ID = update.message.text.strip()
    
    await update.message.reply_text(
        "✅ Product ID updated!\n\n"
        "Enter new <b>Phone ID</b>:",
        parse_mode='HTML'
    )
    return WAITING_PHONE_ID

async def receive_phone_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    global MAYTAPI_PHONE_ID
    MAYTAPI_PHONE_ID = update.message.text.strip()
    
    await update.message.reply_text(
        "✅ Phone ID updated!\n\n"
        "Enter new <b>API Token</b>:",
        parse_mode='HTML'
    )
    return WAITING_API_TOKEN

async def receive_api_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    global MAYTAPI_TOKEN
    MAYTAPI_TOKEN = update.message.text.strip()
    
    await update.message.reply_text(
        "<b>✅ Admin Credentials Updated!</b>\n\n"
        "Your default credentials have been updated.",
        parse_mode='HTML'
    )
    return ConversationHandler.END

async def cancel_creds(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("<b>❌ Update Cancelled</b>", parse_mode='HTML')
    return ConversationHandler.END

# ==================== MAIN ====================

def main() -> None:
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        print("=" * 60)
        print("ERROR: Please configure TELEGRAM_BOT_TOKEN in the file!")
        print("=" * 60)
        return
    
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Setup conversation handler for regular users
    setup_conv = ConversationHandler(
        entry_points=[CommandHandler("setup", setup_start)],
        states={
            WAITING_PRODUCT_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, setup_receive_product_id)
            ],
            WAITING_PHONE_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, setup_receive_phone_id)
            ],
            WAITING_API_TOKEN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, setup_receive_api_token)
            ],
        },
        fallbacks=[CommandHandler("cancel", setup_cancel)],
    )
    
    # Credentials update conversation for admin
    creds_conv = ConversationHandler(
        entry_points=[CommandHandler("setcreds", setcreds_start)],
        states={
            WAITING_PRODUCT_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_product_id)
            ],
            WAITING_PHONE_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone_id)
            ],
            WAITING_API_TOKEN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_api_token)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_creds)],
    )
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("premium", premium_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CommandHandler("stop", stop_command))
    
    # Admin commands
    application.add_handler(CommandHandler("adminstatus", admin_status_command))
    application.add_handler(CommandHandler("approve", approve_premium_command))
    application.add_handler(CommandHandler("reject", reject_premium_command))
    application.add_handler(CommandHandler("premiumusers", premium_users_command))
    application.add_handler(CommandHandler("removepremium", remove_premium_command))
    application.add_handler(CommandHandler("testapi", test_api_command))
    
    # Conversations
    application.add_handler(setup_conv)
    application.add_handler(creds_conv)
    
    # Callback handler
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # This should be LAST - catches all text messages as numbers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_numbers))
    
    # Run
    print("=" * 60)
    print("🤖 WhatsApp Number Checker Bot - Premium Edition v2 (FIXED)")
    print("=" * 60)
    print(f"✅ Bot Token: {'Set' if TELEGRAM_BOT_TOKEN != 'YOUR_TELEGRAM_BOT_TOKEN_HERE' else 'Not Set'}")
    print(f"✅ Default Product ID: {'Set' if MAYTAPI_PRODUCT_ID != 'YOUR_PRODUCT_ID_HERE' else 'Not Set'}")
    print(f"✅ Default Phone ID: {'Set' if MAYTAPI_PHONE_ID != 'YOUR_PHONE_ID_HERE' else 'Not Set'}")
    print(f"✅ Default API Token: {'Set' if MAYTAPI_TOKEN != 'YOUR_API_TOKEN_HERE' else 'Not Set'}")
    print(f"💰 Premium Price: ${PREMIUM_PRICE} for {PREMIUM_DURATION_DAYS} days")
    print(f"⚡ Concurrent Requests: {CONCURRENT_REQUESTS}")
    print(f"📊 Progress Update Every: {BATCH_SIZE} numbers")
    print(f"🔧 Admin Test Command: /testapi")
    print("=" * 60)
    print("🚀 Bot is running... Press Ctrl+C to stop.")
    print("=" * 60)
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
