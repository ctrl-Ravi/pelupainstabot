from telegram import Update, ReplyKeyboardMarkup, error
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
import subprocess
import os
import shutil
import json
import requests
import re
from bs4 import BeautifulSoup

# ----- ADD ON TOP -----
import yt_dlp
import instaloader

import time
import asyncio

COOLDOWN = {}
STATS_FILE = "stats.json"

# -------- STATS --------

if not os.path.exists(STATS_FILE):
    json.dump({"download":0,"video":0,"audio":0,"caption":0}, open(STATS_FILE,"w"))

def stat_load():
    return json.load(open(STATS_FILE))

def stat_save(d):
    json.dump(d, open(STATS_FILE,"w"))

def add_stat(t):
    d = stat_load()
    d["download"] += 1
    d[t] += 1
    stat_save(d)

# -------- REWARD BULK LIMIT --------

def user_bulk(uid):
    if get_invite(uid) >= 5:
        return 10
    return MAX_BULK

# -------- COOLDOWN --------

def check_cool(uid):
    now = time.time()
    last = COOLDOWN.get(uid, 0)

    if now - last < 10:
        return False

    COOLDOWN[uid] = now
    return True

import logging
from flask import Flask
from threading import Thread

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================== CONFIG ==================

# Load from Environment Variables
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    print("❌ Error: TOKEN not found in environment variables")
    # You might want to exit here, or handle it. 
    # For now, we'll let it fail effectively at Application.builder() or just exist.

BOT_USERNAME = os.getenv("BOT_USERNAME", "pelupabot")
STORE = os.getenv("STORE", "https://store.pelupa.in")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0")) # Default to 0 if not set
COUPON = os.getenv("COUPON", "FREE100")

# ================== FLASK KEEP-ALIVE ==================

app_flask = Flask(__name__)

@app_flask.route('/')
def health_check():
    return "Bot is alive!", 200

def run_flask():
    # Render provides PORT via env var
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host='0.0.0.0', port=port)

def ping_self():
    import time
    import requests
    
    # Wait for Flask to start
    time.sleep(5)
    
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        # Fallback to localhost if no external URL (for testing)
        # But user specifically asked for RENDER_EXTERNAL_URL logic
        print("KeepAlive: RENDER_EXTERNAL_URL not found")
        return

    print("KeepAlive URL:", url)

    while True:
        try:
            requests.get(url, timeout=10)
            print("KeepAlive ping sent")
        except Exception as e:
            print("KeepAlive error:", e)

        time.sleep(600)

def keep_alive():
    t1 = Thread(target=run_flask)
    t1.start()
    
    t2 = Thread(target=ping_self)
    t2.start()


MAX_BULK = 5
DATA_FILE = "ref.json"

# ================ DATABASE ==================

if not os.path.exists(DATA_FILE):
    json.dump({"users": {}}, open(DATA_FILE, "w"))

def load():
    return json.load(open(DATA_FILE))

def save(data):
    json.dump(data, open(DATA_FILE, "w"))

USER_MODE = {}


MENU = ReplyKeyboardMarkup(
    [["🎥 Video", "🎵 Audio", "📝 Caption"]],
    resize_keyboard=True
)

# =============== REFERRAL ===================

def add_user(uid, ref=None):
    data = load()
    u = str(uid)

    if u not in data["users"]:
        data["users"][u] = {"invite": 0}

        if ref and ref in data["users"]:
            data["users"][ref]["invite"] += 1

    save(data)

def get_invite(uid):
    data = load()
    return data["users"].get(str(uid), {}).get("invite", 0)

def reward(uid):
    c = get_invite(uid)

    if c >= 20:
        return "🎁 FREE Bundle Coupon"
    if c >= 10:
        return "🚀 No Ads Unlocked"
    if c >= 5:
        return "📦 Bulk 10 Enabled"

    return "No reward yet"

def is_admin(uid):
    return uid == ADMIN_ID


# =============== CAPTION ====================

def get_caption(url):

    # oEmbed
    try:
        api = f"https://api.instagram.com/oembed?url={url}"
        r = requests.get(api)
        if r.status_code == 200:
            return r.json().get("title")
    except:
        pass

    # Scraping
    try:
        html = requests.get(url, headers={"User-Agent": "Mozilla"}).text
        soup = BeautifulSoup(html, "html.parser")
        m = soup.find("meta", property="og:title")
        if m:
            return m["content"]
    except:
        pass

    return "No caption found"

# =============== DOWNLOAD ===================

# def download(url):

#     L = instaloader.Instaloader(
#         download_video_thumbnails=False,
#         save_metadata=False
#     )

#     shortcode = url.split("/")[-2]
#     post = instaloader.Post.from_shortcode(L.context, shortcode)

#     os.makedirs("downloads", exist_ok=True)
#     L.download_post(post, target="downloads")

#     for f in os.listdir("downloads"):
#         if f.endswith(".mp4"):
#             return os.path.join("downloads", f)

#     return None


def get_shortcode(url):
    m = re.search(r"/(reel|p)/([^/?]+)", url)
    if m:
        return m.group(2)
    return None

def find_mp4(base="downloads"):
    print(f"[DEBUG] Searching in {base}")
    for root, dirs, files in os.walk(base):
        print(f"[DEBUG] Checking {root}, files: {files}")
        for f in files:
            # Search for video files first, then image files
            if f.lower().endswith((".mp4", ".m4a", ".mp3", ".webm")):
                return os.path.join(root, f)
    
    # If no video found, look for image files (for carousel/image posts)
    for root, dirs, files in os.walk(base):
        for f in files:
            if f.lower().endswith((".jpg", ".jpeg", ".png")):
                return os.path.join(root, f)
    
    return None


# =============== INSTALOADER FALLBACK =================

L = instaloader.Instaloader(
    download_video_thumbnails=False,
    save_metadata=False,
    download_geotags=False,
    download_comments=False,
    compress_json=False
)

def init_instaloader():
    user = os.getenv("INSTAGRAM_USER")
    pwd = os.getenv("INSTAGRAM_PASS")
    
    if user and pwd:
        try:
            print(f"[INIT] Logging in to Instaloader as {user}...")
            L.login(user, pwd)
            print("[INIT] Instaloader login success!")
        except Exception as e:
            print(f"[ERROR] Instaloader login failed: {e}")
    else:
        print("[INIT] No Instagram credentials found. Instaloader will run anonymously (limited).")

def download_instaloader(url, target_dir):
    try:
        shortcode = get_shortcode(url)
        if not shortcode:
            print("[ERROR] Could not extract shortcode for Instaloader")
            return None

        print(f"[DEBUG] Instaloader fallback for {shortcode}...")
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        
        # Download to specific target directory
        L.download_post(post, target=target_dir)
        
        return True
    except Exception as e:
        print(f"[ERROR] Instaloader fallback failed: {e}")
        return False


def download(url, uid, mode="video"):
    target = f"downloads/{uid}"
    print(f"[DEBUG] Starting download for {uid} in {target}")
    os.makedirs(target, exist_ok=True)
    
    # Clean possible query params for better compatibility
    if "?" in url:
        url = url.split("?")[0]


    # Mobile client simulation to avoid blocks
    # "ios" seems to work better for some endpoints
    extractor_args = {
        'instagram': {
            'platform': 'ios',
            'version': '280.0.0.24.116',
        }
    }

    ydl_opts = {
        'outtmpl': f'{target}/%(id)s.%(ext)s',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'extractor_args': extractor_args,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
            'Accept-Language': 'en-US,en;q=0.9',
        },
        'no_cache_dir': True
    }

    # Add cookies if available
    if not os.path.exists("cookies.txt"):
        # Allow loading cookies from ENV for secure deployment
        cookies_content = os.getenv("COOKIES_TXT_CONTENT")
        if cookies_content:
            try:
                with open("cookies.txt", "w") as f:
                    f.write(cookies_content)
                print("[INIT] Created cookies.txt from environment variable")
            except Exception as e:
                print(f"[ERROR] Failed to create cookies.txt from env: {e}")

    if os.path.exists("cookies.txt"):
        print(f"[DEBUG] Using cookies.txt for {url}")
        ydl_opts['cookiefile'] = "cookies.txt"

    if mode == "audio":
        # Download best audio directly (m4a/mp3)
        ydl_opts['format'] = 'bestaudio/best'
    else:
        # Download best video (mp4 preferred)
        ydl_opts['format'] = 'best[ext=mp4][protocol^=http]/best[ext=mp4]/best'

    # Retry mechanism
    MAX_RETRIES = 3
    for attempt in range(MAX_RETRIES):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                print(f"[DEBUG] Downloading with yt-dlp (Attempt {attempt+1}): {url}")
                ydl.download([url])
                print(f"[DEBUG] yt-dlp finished.")
                break # Success
        except Exception as e:
            print(f"[ERROR] yt-dlp failed (Attempt {attempt+1}): {e}")
            if attempt == MAX_RETRIES - 1:
                pass # Last attempt failed
            else:
                time.sleep(2) # Wait before retry
    
    # Check if we found a video (from yt-dlp)
    found = find_mp4(target)
    
    # Fallback 1: Instaloader (Try to get video if yt-dlp failed)
    if not found:
        print("[DEBUG] yt-dlp failed. Trying Instaloader...")
        if download_instaloader(url, target):
            found = find_mp4(target)

    # Fallback 2: Image (Last resort if no video found)
    if not found:
        print("[DEBUG] No video found, checking for images...")
        try:
            # Fallback for images: Simple requests + bs4 to findog:image
            # We use a random UA to avoid 403 if possible
            MAX_RETRIES = 3
            import random
            user_agents = [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Safari/605.1.15',
                'Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1'
            ]
            
            headers = {"User-Agent": random.choice(user_agents)}
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                meta_img = soup.find("meta", property="og:image")
                if meta_img:
                    img_url = meta_img["content"]
                    print(f"[DEBUG] Found image URL: {img_url}")
                    # Download image
                    img_data = requests.get(img_url, headers=headers).content
                    img_path = os.path.join(target, "image.jpg")
                    with open(img_path, "wb") as f:
                        f.write(img_data)
                    found = img_path
        except Exception as e:
             print(f"[ERROR] Image fallback failed: {e}")

    print(f"[DEBUG] Found file: {found}")
    return found





# =============== AUDIO ======================






# =============== BULK =======================

def parse_links(text, uid):
    links = []
    for l in text.split("\n"):
        l = l.strip()
        if "instagram.com" in l:
            links.append(l)
    return links[: user_bulk(uid)]


# =============== COMMANDS ===================

async def start(update: Update, context):

    uid = update.effective_user.id
    ref = context.args[0] if context.args else None

    add_user(uid, ref)
    USER_MODE[uid] = "video"

    await update.message.reply_text(
        f"👋 <b>Welcome to Insta Tool Bot</b>\n\n"
        "I can help you download <b>Reels, Videos, and Audio</b> from Instagram without watermarks.\n\n"
        "👇 <b>Choose an option:</b>\n\n"
        "/video - 🎥 Download Video\n"
        "/audio - 🎵 Extract Audio\n"
        "/caption - 📝 Get Caption\n\n"
        "🎁 /refer to Earn Rewards",
        reply_markup=MENU,
        parse_mode='HTML'
    )

async def set_video(update, context):
    USER_MODE[update.effective_user.id] = "video"
    await update.message.reply_text("🎥 <b>Video mode active</b>\nSend Reel/Post link to download.", parse_mode='HTML')

async def set_audio(update, context):
    USER_MODE[update.effective_user.id] = "audio"
    await update.message.reply_text("🎵 <b>Audio mode active</b>\nSend Reel link to extract music.", parse_mode='HTML')

async def set_caption(update, context):
    USER_MODE[update.effective_user.id] = "caption"
    await update.message.reply_text("📝 <b>Caption mode active</b>\nSend Post link to get text.", parse_mode='HTML')

async def help(update, context):

    msg = (
        "📖 <b>HOW TO USE</b>\n\n"
        "1️⃣ <b>Choose Mode</b>\n"
        "/video - Download Reels/Videos\n"
        "/audio - Extract pure audio from Reels\n"
        "/caption - Copy post caption\n\n"
        "2️⃣ <b>Send Link</b>\n"
        "Paste any Instagram link (Reel, Post, IGTV).\n"
        "<i>You can send multiple links at once!</i>\n\n"
        "3️⃣ <b>Get Result</b>\n"
        "I will instantly send the file without watermarks.\n\n"
        "━━━━━━━━━━━━━━━━\n"
        f"🛍️ <a href='{STORE}'><b>Visit Store</b></a>\n"
        "🎁 /refer - Earn Free Rewards"
    )

    await update.message.reply_text(msg, parse_mode='HTML', disable_web_page_preview=True)


async def about(update, context):

    msg = (
        f"🤖 <b>About Insta Tool Bot</b>\n\n"
        "We provide the fastest Instagram downloader on Telegram.\n\n"
        "✅ <b>Features:</b>\n"
        "• High Quality Downloads\n"
        "• No Watermarks\n"
        "• Music Extraction\n"
        "• Bulk Downloading\n"
        "• 99.9% Uptime\n\n"
        "👨‍💻 <b>Developer:</b> @pelupabot\n"
        f"🌐 <b>Website:</b> <a href='{STORE}'>store.pelupa.in</a>"
    )

    await update.message.reply_text(msg, parse_mode='HTML', disable_web_page_preview=True)


async def store(update, context):
    await update.message.reply_text(
        "🚀 <b>Grow on Instagram Faster!</b>\n\n"
        "🔥 <b>Reels Bundles</b> (Viral Content)\n"
        "🔥 <b>Caption Packs</b> (Engagement)\n"
        "🔥 <b>Canva Templates</b> (Professional Design)\n\n"
        f"👉 <a href='{STORE}'><b>Click Here to Visit Store</b></a>\n\n"
        "<i>✅ Instant Access • Lifetime Use • Secure Payment</i>",
        parse_mode='HTML'
    )

async def bulk(update, context):

    msg = (
        "📦 <b>BULK DOWNLOAD GUIDE</b>\n\n"
        "Download up to 5 posts at once!\n\n"
        "1. Select /video or /audio mode.\n"
        "2. Paste links line-by-line:\n\n"
        "<code>https://instagram.com/p/Example1</code>\n"
        "<code>https://instagram.com/p/Example2</code>\n"
        "<code>https://instagram.com/p/Example3</code>\n\n"
        "<i>🚀 Premium users can download more!</i>"
    )

    await update.message.reply_text(msg, parse_mode='HTML')


async def stats(update, context):

    uid = update.effective_user.id

    # ---- ONLY ADMIN CAN SEE ----
    if uid != ADMIN_ID:
        await update.message.reply_text("❌ <b>Access Denied</b>", parse_mode='HTML')
        return

    d = stat_load()

    msg = f"""
📊 <b>BOT STATISTICS</b>

Downloads: <b>{d['download']}</b>
🎥 Video: <b>{d['video']}</b>
🎵 Audio: <b>{d['audio']}</b>
📝 Caption: <b>{d['caption']}</b>
"""
    await update.message.reply_text(msg, parse_mode='HTML')



async def admin(update, context):

    if not is_admin(update.effective_user.id):
        return

    msg = """
🔐 ADMIN PANEL

/broadcast – Send to all users  
/promo – Quick promo  
/stats – View analytics  
/coupon CODE – Set coupon
"""
    await update.message.reply_text(msg)


async def broadcast(update, context):

    if not is_admin(update.effective_user.id):
        return

    text = update.message.text.replace("/broadcast", "").strip()

    if not text:
        await update.message.reply_text("Use:\n/broadcast Your promo text")
        return

    data = load()
    users = data["users"]

    sent = 0

    for uid in users:
        try:
            await context.bot.send_message(
                chat_id=int(uid),
                text=text
            )
            sent += 1
        except:
            pass

    await update.message.reply_text(f"✅ Sent to {sent} users")


async def promo(update, context):

    if not is_admin(update.effective_user.id):
        return

    msg = f"""
🚀 Grow on Instagram Faster

🔥 Reels Bundles  
🔥 Caption Packs  
🔥 Canva Templates  

👉 {STORE}

Instant Access • Lifetime Use
"""
    await update.message.reply_text(msg)


async def coupon(update, context):

    global COUPON

    if not is_admin(update.effective_user.id):
        return

    try:
        COUPON = context.args[0]
        await update.message.reply_text(f"✅ <b>Coupon Set:</b> <code>{COUPON}</code>", parse_mode='HTML')
    except:
        await update.message.reply_text("ℹ️ <b>Usage:</b> <code>/coupon CODE</code>", parse_mode='HTML')


# =============== REFER ======================

async def refer(update, context):

    uid = update.effective_user.id

    link = f"https://t.me/{BOT_USERNAME}?start={uid}"

    await update.message.reply_text(
        f"🎉 <b>EARN FREE REWARDS</b>\n\n"
        "Invite friends and unlock premium features!\n\n"
        f"🔗 <b>Your Referral Link:</b>\n<code>{link}</code>\n\n"
        f"👥 <b>Your Invites:</b> {get_invite(uid)}\n"
        f"🎁 <b>Current Status:</b> {reward(uid)}\n\n"
        "<b>Milestones:</b>\n"
        "🔹 5 Invites → Bulk Download (10 Links)\n"
        "🔹 10 Invites → <b>No Ads</b>\n"
        "🔹 20 Invites → 🎁 <b>Free Bundle Coupon</b>",
        parse_mode='HTML'
    )



# =============== MENU =======================

async def menu(update, context):

    t = update.message.text

    if "Video" in t:
        await set_video(update, context)
    elif "Audio" in t:
        await set_audio(update, context)
    elif "Caption" in t:
        await set_caption(update, context)
    else:
        await handle(update, context)


# =============== MAIN =======================

async def handle(update: Update, context):

    text = update.message.text
    uid = update.effective_user.id
    mode = USER_MODE.get(uid, "video")

    # ---- Anti Spam ----
    if not check_cool(uid):
        await update.message.reply_text("⏳ <b>Please wait 10 seconds</b> before next request.", parse_mode='HTML')
        return

    links = parse_links(text, uid)

    if not links:
        await update.message.reply_text("❌ <b>Invalid Link</b>\nPlease send a valid Instagram URL.", parse_mode='HTML')
        return

    msg_processing = await update.message.reply_text(
        f"<b>⚡ Processing your request...</b>\n"
        f"🔗 <b>Links:</b> {len(links)}\n"
        f"⚙️ <b>Mode:</b> {mode.title()}\n\n"
        "<i>Fetching content from Instagram...</i>",
        parse_mode='HTML'
    )


    for url in links:
        video = None
        try:
            # Run blocking download in a separate thread
            # download now returns the file path, whether video or audio
            file_path = await asyncio.to_thread(download, url, uid, mode)
            
            # Check if download succeeded
            if not file_path:
                # Remove parse_mode for error message to avoid issues with special chars in URL
                await update.message.reply_text(
                    f"❌ Failed: {url}\nPossible Private Account or Invalid Link"
                )
                continue

            # Determine file type
            is_image = file_path.lower().endswith((".jpg", ".jpeg", ".png"))
            is_audio = file_path.lower().endswith((".m4a", ".mp3", ".webm"))
            
            caption_text = (
                f"🎥 <b>Downloaded via @{BOT_USERNAME}</b>\n"
                f"🚀 <a href='{STORE}'>Get Premium Bundles</a>"
            )

            # ----- VIDEO -----
            if mode == "video":
                try:
                    if is_image:
                        await update.message.reply_photo(
                            photo=open(file_path, "rb"),
                            caption=caption_text,
                            parse_mode='HTML'
                        )
                    else:
                        print(f"[DEBUG] Uploading video: {file_path}")
                        await update.message.reply_video(
                            video=open(file_path, "rb"),
                            caption=caption_text,
                            parse_mode='HTML',
                            supports_streaming=True,
                            read_timeout=300, 
                            write_timeout=300
                        )
                        print("[DEBUG] Upload success")
                except error.TimedOut:
                    # Ignore timeout if the user ultimately receives the file
                    print(f"[WARN] Upload timed out for {url}, but likely sent.")
                
                add_stat("video")

            # ----- AUDIO -----
            elif mode == "audio":
                # Audio is already downloaded by download()
                try:
                    await update.message.reply_audio(
                        audio=open(file_path, "rb"),
                        title=f"Insta Audio {uid}",
                        performer=f"@{BOT_USERNAME}",
                        caption=f"🎵 <b>Extracted Audio</b>\n👉 {STORE}",
                        parse_mode='HTML',
                        read_timeout=60,
                        write_timeout=60
                    )
                except error.TimedOut:
                        print(f"[WARN] Audio upload timed out for {url}")
                
                add_stat("audio")

            # ----- CAPTION -----
            elif mode == "caption":
                cap = get_caption(url)
                # Escape HTML special chars in caption just in case
                import html
                cap = html.escape(cap)

                msg = f"""
📝 <b>CAPTION</b>

{cap}

🚀 <a href='{STORE}'>Grow with Bundles</a>
"""
                await update.message.reply_text(msg, parse_mode='HTML')
                add_stat("caption")

        except Exception as e:
            print(f"Error processing {url}: {e}")
            await update.message.reply_text(f"❌ Error: {e}")
    
    # Delete processing message
    try:
        await msg_processing.delete()
    except:
        pass


    
    # ---- CLEANUP AFTER ALL LINKS ----
    target = f"downloads/{uid}"
    if os.path.exists(target):
        try:
            shutil.rmtree(target)
        except:
            pass

    # ----- PROMO CONTROL -----

    if get_invite(uid) < 10:
        await update.message.reply_text(
            f"🔥 Premium Bundles\n👉 {STORE}"
        )

    # ----- COUPON REWARD -----

    if get_invite(uid) == 20:
        await update.message.reply_text(
            "🎉 CONGRATS!\n\nUse Coupon: FREE100\nat store.pelupa.in"
        )


# =============== RUN ========================

if __name__ == "__main__":
    keep_alive()  # Start Flask server
    init_instaloader() # Login to Instaloader
    
    # Custom request settings for stability
    request = HTTPXRequest(
        connection_pool_size=10,
        read_timeout=60,
        write_timeout=60,
        connect_timeout=60,
        pool_timeout=60,
    )

    app = (
        Application.builder()
        .token(TOKEN)
        .request(request)
        .concurrent_updates(True)
        .build()
    )

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("video", set_video))
app.add_handler(CommandHandler("audio", set_audio))
app.add_handler(CommandHandler("caption", set_caption))
app.add_handler(CommandHandler("help", help))
app.add_handler(CommandHandler("about", about))
app.add_handler(CommandHandler("store", store))
app.add_handler(CommandHandler("refer", refer))
app.add_handler(CommandHandler("bulk", bulk))
app.add_handler(CommandHandler("stats", stats))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(CommandHandler("broadcast", broadcast))
app.add_handler(CommandHandler("promo", promo))
app.add_handler(CommandHandler("coupon", coupon))


app.add_handler(MessageHandler(filters.TEXT, menu))

# Start polling
print("✅ Bot is running...")
app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


