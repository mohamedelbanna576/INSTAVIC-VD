"""
INSTAVIC VD — Premium Instagram & YouTube Downloader
FastAPI Backend Server
"""

import os
import sys
import re
import uuid
import shutil
import zipfile
import asyncio
import json
import time
import random
import concurrent.futures
from functools import partial
from pathlib import Path
from typing import Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    FileResponse,
    StreamingResponse,
    JSONResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import instaloader
import yt_dlp

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(title="INSTAVIC VD", version="2.2.0")

# Setup Paths for PyInstaller EXE support
# BASE_DIR is internal (where static files are bundled)
# EXE_DIR is external (where the EXE, cookies, and downloads will live)
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys._MEIPASS)
    EXE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent
    EXE_DIR = BASE_DIR

# Force /tmp on Vercel, use local for Dev/EXE
IS_VERCEL = os.environ.get("VERCEL", "0") == "1"

def get_base_writable_dir() -> Path:
    """Determine writable base directory (local, /tmp on Vercel, or next to EXE)."""
    if IS_VERCEL:
        tmp_dir = Path("/tmp/instavic")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        return tmp_dir
    return EXE_DIR

def get_cookies_path() -> Optional[str]:
    """Get the path to the cookies file, creating it from ENV if necessary."""
    writable_dir = get_base_writable_dir()
    cookies_file = writable_dir / "cookies.txt"
    
    # If the file already exists (local or already created in /tmp), use it
    if cookies_file.exists():
        return str(cookies_file)
    
    # Otherwise try to create it from ENV
    env_cookies = os.environ.get("YOUTUBE_COOKIES")
    if env_cookies:
        try:
            cookies_file.write_text(env_cookies, encoding='utf-8')
            return str(cookies_file)
        except Exception as e:
            print(f"Failed to write cookies to {cookies_file}: {e}")
            
    # Fallback to local cookies.txt if it exists in BASE_DIR (not on Vercel)
    local_cookies = BASE_DIR / "cookies.txt"
    if local_cookies.exists():
        return str(local_cookies)
        
    return None

# Paths (now stable since mkdir is handled in get_base_writable_dir)
def get_downloads_dir() -> Path:
    d = get_base_writable_dir() / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_config_file() -> Path:
    return get_base_writable_dir() / "config.json"

def get_proxies_file() -> Path:
    return get_base_writable_dir() / "proxies.txt"

# Static files (Mounted from bundled BASE_DIR)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

@app.get("/api/health")
async def health_check():
    """Verify that the app is alive and check current write path."""
    return {
        "status": "online",
        "version": "2.2.1",
        "is_vercel": IS_VERCEL,
        "writable_base": str(get_base_writable_dir())
    }


# Instaloader instance (reusable)
L = instaloader.Instaloader(
    download_pictures=False,
    download_videos=True,
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False,
    post_metadata_txt_pattern="",
    filename_pattern="{shortcode}",
)
# Hard 15-second socket timeout — if Instagram doesn't respond, fail immediately
L.context.user_agent = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
L.context.max_retries = 0

# Patch the requests session to enforce a 15s timeout on all calls
import instaloader.exceptions
_orig_get = L.context._session.get
_orig_post = L.context._session.post

def _get_with_timeout(*args, **kwargs):
    kwargs.setdefault('timeout', 15)
    return _orig_get(*args, **kwargs)

def _post_with_timeout(*args, **kwargs):
    kwargs.setdefault('timeout', 15)
    return _orig_post(*args, **kwargs)

L.context._session.get = _get_with_timeout
L.context._session.post = _post_with_timeout


# ---------------------------------------------------------------------------
# State Management (Config & Proxy)
# ---------------------------------------------------------------------------

if not get_config_file().exists():
    with open(get_config_file(), "w") as f:
        json.dump({"instagram_session_id": ""}, f)

def load_config() -> dict:
    try:
        with open(get_config_file(), "r") as f:
            return json.load(f)
    except Exception:
        return {"instagram_session_id": ""}

def save_config(data: dict):
    with open(get_config_file(), "w") as f:
        json.dump(data, f)

def get_random_proxy() -> Optional[str]:
    """Retrieve a random proxy line from proxies.txt if available."""
    if not get_proxies_file().exists():
        return None
    try:
        with open(get_proxies_file(), "r") as f:
            proxies = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        if proxies:
            return random.choice(proxies)
    except Exception:
        pass
    return None

def apply_auth_and_proxy(cookie_val: str, ctx_proxy: Optional[str] = None):
    """Apply session ID and proxy to the global Instaloader context."""
    if cookie_val:
        L.context._session.cookies.set("sessionid", cookie_val, domain=".instagram.com")
    else:
        L.context._session.cookies.clear(domain=".instagram.com")
    
    if ctx_proxy:
        L.context.proxies = {"http": ctx_proxy, "https": ctx_proxy}
    else:
        L.context.proxies = {}

# In-memory task store for bulk downloads
bulk_tasks: dict = {}

# Thread pool for running blocking downloads without freezing event loop
_thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class SingleDownloadRequest(BaseModel):
    url: str

class LoginRequest(BaseModel):
    session_id: str

class BulkDownloadRequest(BaseModel):
    url: str
    max_posts: Optional[int] = 50  # safety cap

class YouTubeInfoRequest(BaseModel):
    url: str

class YouTubeDownloadRequest(BaseModel):
    url: str
    format_id: str  # yt-dlp format ID chosen by user
    quality_label: str = ""  # human-readable label for filename

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
INSTAGRAM_POST_RE = re.compile(
    r"(?:https?://)?(?:www\.)?instagram\.com/"
    r"(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)",
)
INSTAGRAM_PROFILE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?instagram\.com/"
    r"([A-Za-z0-9_.]+)/?(?:\?.*)?$",
)
YOUTUBE_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/(?:watch\?v=|shorts/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)

def is_youtube_url(url: str) -> bool:
    """Check if the URL is a YouTube video URL."""
    return bool(YOUTUBE_RE.search(url))

def extract_shortcode(url: str) -> Optional[str]:
    """Extract the shortcode from an Instagram post/reel URL."""
    m = INSTAGRAM_POST_RE.search(url)
    return m.group(1) if m else None

def extract_username(url: str) -> Optional[str]:
    """Extract the username from an Instagram profile URL."""
    m = INSTAGRAM_PROFILE_RE.search(url)
    if m:
        username = m.group(1)
        # Filter out known non-profile paths
        reserved = {
            "p", "reel", "reels", "tv", "explore", "stories",
            "accounts", "directory", "developer", "about", "legal",
        }
        if username.lower() not in reserved:
            return username
    return None

def find_video_file(directory: Path) -> Optional[Path]:
    """Find the first video file in a directory."""
    video_extensions = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
    for f in directory.iterdir():
        if f.suffix.lower() in video_extensions:
            return f
    return None

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """Serve the frontend."""
    index_path = STATIC_DIR / "index.html"
    return HTMLResponse(content=index_path.read_text(encoding="utf-8"))


@app.get("/api/config")
async def get_config():
    """Allow frontend to query the current active connection state."""
    conf = load_config()
    return {
        "connected": bool(conf.get("instagram_session_id")),
        "proxy_enabled": get_proxies_file().exists()
    }


@app.post("/api/login")
async def store_session(req: LoginRequest):
    """Store the Instagram session ID cookie to local disk config."""
    sid = req.session_id.strip()
    config = load_config()
    config["instagram_session_id"] = sid
    save_config(config)
    
    if sid:
        return {"success": True, "message": "✅ Session securely saved parameters!"}
    else:
        return {"success": True, "message": "Session cleared. Using anonymous mode."}


@app.post("/api/detect")
async def detect_url_type(req: SingleDownloadRequest):
    """Auto-detect whether a URL is a single post or a profile."""
    url = req.url.strip()
    if extract_shortcode(url):
        return {"type": "single", "value": extract_shortcode(url)}
    elif extract_username(url):
        return {"type": "profile", "value": extract_username(url)}
    else:
        raise HTTPException(status_code=400, detail="Invalid Instagram URL. Please provide a valid post, reel, or profile link.")


@app.post("/api/single")
async def download_single(req: SingleDownloadRequest):
    """Download a single Instagram video — runs in thread pool to avoid blocking."""
    url = req.url.strip()
    shortcode = extract_shortcode(url)

    if not shortcode:
        raise HTTPException(status_code=400, detail="Invalid URL. Please provide a valid Instagram post or reel link.")

    task_id = uuid.uuid4().hex[:12]
    task_dir = get_downloads_dir() / task_id
    task_dir.mkdir(exist_ok=True)
    
    # Grab the current configs for this request
    config_state = load_config()
    session_id = config_state.get("instagram_session_id", "")
    proxy = get_random_proxy()

    loop = asyncio.get_event_loop()
    try:
        # Run the blocking download in a thread so the event loop stays free
        result = await asyncio.wait_for(
            loop.run_in_executor(_thread_pool, partial(_do_single_download, url, shortcode, task_id, task_dir, session_id, proxy)),
            timeout=90.0
        )
        return result
    except asyncio.TimeoutError:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise HTTPException(status_code=504, detail="Download timed out (90s). Instagram may be slow or blocking. Try connecting your account.")
    except HTTPException:
        raise
    except Exception as e:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))


def _do_single_download(url: str, shortcode: str, task_id: str, task_dir: Path, session_id: str, proxy: Optional[str]) -> dict:
    """Blocking download logic — runs in thread pool."""
    # --- Try yt-dlp first ---
    try:
        cookie_opts = {}
        if session_id:
            cookie_opts['http_headers'] = {'Cookie': f'sessionid={session_id}'}

        ydl_opts = {
            'format': 'best[ext=mp4]/best',
            'outtmpl': str(task_dir / '%(id)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'user_agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
            **cookie_opts,
        }
        
        if proxy:
            ydl_opts['proxy'] = proxy
        if session_id:
            ydl_opts['cookiefile'] = None

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            if session_id:
                import http.cookiejar
                cookie = http.cookiejar.Cookie(
                    version=0, name='sessionid', value=session_id,
                    port=None, port_specified=False,
                    domain='.instagram.com', domain_specified=True, domain_initial_dot=True,
                    path='/', path_specified=True, secure=True,
                    expires=None, discard=True, comment=None, comment_url=None, rest={}
                )
                ydl.cookiejar.set_cookie(cookie)

            info = ydl.extract_info(url, download=True)
            if info:
                video_file = find_video_file(task_dir)
                if video_file:
                    return {
                        "success": True,
                        "video": {
                            "filename": video_file.name,
                            "download_url": f"/api/download/{task_id}/{video_file.name}",
                            "caption": (info.get('description') or '')[:200],
                            "date": datetime.fromtimestamp(info['timestamp']).isoformat() if info.get('timestamp') else None,
                            "views": info.get('view_count', 0),
                            "likes": info.get('like_count', 0),
                        },
                    }
    except Exception as e:
        print(f"[yt-dlp] failed: {e}")

    # --- Fallback: Instaloader ---
    try:
        apply_auth_and_proxy(session_id, proxy)
        post = instaloader.Post.from_shortcode(L.context, shortcode)

        if not post.is_video:
            raise HTTPException(status_code=400, detail="This post does not contain a video.")

        L.download_post(post, target=str(task_dir))
        video_file = find_video_file(task_dir)

        if not video_file:
            raise HTTPException(status_code=500, detail="Download finished but no video file found.")

        return {
            "success": True,
            "video": {
                "filename": video_file.name,
                "download_url": f"/api/download/{task_id}/{video_file.name}",
                "caption": (post.caption or '')[:200],
                "date": post.date_utc.isoformat() if post.date_utc else None,
                "views": post.video_view_count,
                "likes": post.likes,
            },
        }

    except HTTPException:
        raise
    except instaloader.exceptions.InstaloaderException as e:
        msg = str(e).lower()
        if 'rate' in msg or '429' in msg or '401' in msg or 'login' in msg or 'unauthorized' in msg:
            raise HTTPException(status_code=403, detail="Instagram is blocking automated requests from Vercel's IP. To bypass this, please click 'Connect Account' at the top left and paste your Instagram Session ID.")
        elif 'private' in msg:
            raise HTTPException(status_code=403, detail="This post is from a private account.")
        elif 'not found' in msg or '404' in msg:
            raise HTTPException(status_code=404, detail="Post not found or was deleted.")
        else:
            raise HTTPException(status_code=500, detail=f"Instagram blocked the download: {e}. Please use 'Connect Account' to bypass restrictions.")
    except Exception as e:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"All download methods blocked by Instagram. Please click 'Connect Account' and provide your Session ID to fix this! Error: {e}")



@app.post("/api/bulk")
async def start_bulk_download(req: BulkDownloadRequest):
    """Start a bulk download for all videos from a public profile."""
    url = req.url.strip()
    username = extract_username(url)

    if not username:
        raise HTTPException(
            status_code=400,
            detail="Invalid profile URL. Please provide a valid Instagram profile link.",
        )

    task_id = uuid.uuid4().hex[:12]
    task_dir = get_downloads_dir() / task_id
    task_dir.mkdir(exist_ok=True)

    max_posts = min(req.max_posts or 50, 200)  # hard cap at 200

    config_state = load_config()
    session_id = config_state.get("instagram_session_id", "")
    proxy = get_random_proxy()

    # Initialize task state
    bulk_tasks[task_id] = {
        "status": "starting",
        "username": username,
        "total": 0,
        "downloaded": 0,
        "videos": [],
        "errors": [],
        "done": False,
    }

    # Run bulk download in a real thread
    _thread_pool.submit(bulk_download_worker, task_id, username, task_dir, max_posts, session_id, proxy)

    return {"success": True, "task_id": task_id, "username": username}


def bulk_download_worker(task_id: str, username: str, task_dir: Path, max_posts: int, session_id: str, proxy: Optional[str]):
    """Background worker that downloads all videos from a profile."""
    task = bulk_tasks.get(task_id)
    if not task:
        return

    apply_auth_and_proxy(session_id, proxy)

    try:
        try:
            profile = instaloader.Profile.from_username(L.context, username)
        except Exception as e:
            err = str(e)
            if 'timeout' in err.lower() or 'timed out' in err.lower() or 'ConnectionError' in err:
                task["status"] = "error"
                task["errors"].append("Instagram is not responding (timeout). Your IP may be rate-limited. Click 'Connect Account' to fix this.")
                task["done"] = True
                return
            raise

        if profile.is_private:
            task["status"] = "error"
            task["errors"].append("This profile is private. Only public profiles can be scraped.")
            task["done"] = True
            return

        task["status"] = "scanning"

        # Collect video posts
        video_posts = []
        post_count = 0
        for post in profile.get_posts():
            if post_count >= max_posts:
                break
            if post.is_video:
                video_posts.append(post)
            post_count += 1

        task["total"] = len(video_posts)
        task["status"] = "downloading"

        if not video_posts:
            task["status"] = "complete"
            task["errors"].append("No video posts found on this profile.")
            task["done"] = True
            return

        for i, post in enumerate(video_posts):
            try:
                post_dir = task_dir / post.shortcode
                post_dir.mkdir(exist_ok=True)
                L.download_post(post, target=str(post_dir))

                video_file = find_video_file(post_dir)
                if video_file:
                    # Move video to task_dir root with a clean name
                    final_name = f"{post.shortcode}.mp4"
                    final_path = task_dir / final_name
                    shutil.move(str(video_file), str(final_path))
                    # Clean up post subdirectory
                    shutil.rmtree(post_dir, ignore_errors=True)

                    task["videos"].append({
                        "filename": final_name,
                        "download_url": f"/api/download/{task_id}/{final_name}",
                        "caption": (post.caption or "")[:200],
                        "date": post.date_utc.isoformat() if post.date_utc else None,
                        "shortcode": post.shortcode,
                    })
                    task["downloaded"] = i + 1

                # Small delay to avoid rate limiting across concurrent scrapes
                time.sleep(2.0)

            except instaloader.exceptions.InstaloaderException as e:
                error_msg = str(e).lower()
                if "rate" in error_msg or "429" in error_msg:
                    task["errors"].append(f"Rate limited at video {i + 1}. Stopping.")
                    break
                task["errors"].append(f"Failed to download video {post.shortcode}: {e}")
                task["downloaded"] = i + 1

            except Exception as e:
                task["errors"].append(f"Error on video {i + 1}: {e}")
                task["downloaded"] = i + 1

        task["status"] = "complete"
        task["done"] = True

    except instaloader.exceptions.InstaloaderException as e:
        error_msg = str(e).lower()
        if "not found" in error_msg or "404" in error_msg:
            task["status"] = "error"
            task["errors"].append(f"Profile '{username}' not found. Check the URL.")
        elif "private" in error_msg or "login" in error_msg:
            task["status"] = "error"
            task["errors"].append("This profile is private. Only public profiles can be downloaded.")
        elif "rate" in error_msg or "429" in error_msg or "too many" in error_msg:
            task["status"] = "error"
            task["errors"].append("Instagram is rate-limiting this IP. Click 'Connect Account' at the top to fix this — paste your session cookie and downloads will work immediately.")
        else:
            task["status"] = "error"
            task["errors"].append(f"Instagram error: {e}. Try clicking 'Connect Account'.")
        task["done"] = True

    except Exception as e:
        err = str(e)
        if 'timeout' in err.lower() or 'timed out' in err.lower():
            task["status"] = "error"
            task["errors"].append("Request timed out. Instagram is blocking this IP. Click 'Connect Account' to authenticate and bypass this.")
        else:
            task["status"] = "error"
            task["errors"].append(f"Error: {e}")
        task["done"] = True


@app.get("/api/bulk/status/{task_id}")
async def bulk_status_sse(task_id: str):
    """SSE endpoint for bulk download progress."""
    if task_id not in bulk_tasks:
        raise HTTPException(status_code=404, detail="Task not found.")

    async def event_stream():
        while True:
            task = bulk_tasks.get(task_id)
            if not task:
                break

            data = json.dumps({
                "status": task["status"],
                "total": task["total"],
                "downloaded": task["downloaded"],
                "videos": task["videos"],
                "errors": task["errors"],
                "done": task["done"],
            })
            yield f"data: {data}\n\n"

            if task["done"]:
                break

            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/download/{task_id}/{filename:path}")
async def download_file(task_id: str, filename: str):
    """Serve a downloaded video file."""
    # Sanitize to prevent path traversal
    safe_filename = Path(filename).name
    file_path = get_downloads_dir() / task_id / safe_filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found.")

    # Use ASCII-safe filename for Content-Disposition to avoid latin-1 encoding errors
    from urllib.parse import quote
    ascii_safe = safe_filename.encode('ascii', errors='replace').decode('ascii')
    utf8_encoded = quote(safe_filename)

    # Detect media type based on extension
    ext = safe_filename.rsplit('.', 1)[-1].lower() if '.' in safe_filename else ''
    media_types = {'mp4': 'video/mp4', 'mp3': 'audio/mpeg', 'm4a': 'audio/mp4', 'webm': 'video/webm', 'mkv': 'video/x-matroska'}
    media_type = media_types.get(ext, 'application/octet-stream')

    return FileResponse(
        path=str(file_path),
        filename=safe_filename,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename=\"{ascii_safe}\"; filename*=UTF-8''{utf8_encoded}"},
    )



@app.post("/api/bulk/zip/{task_id}")
async def create_zip(task_id: str):
    """Create a ZIP archive of all downloaded videos for a bulk task."""
    task = bulk_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    task_dir = get_downloads_dir() / task_id
    if not task_dir.exists():
        raise HTTPException(status_code=404, detail="Download folder not found.")

    zip_name = f"{task.get('username', 'videos')}_{task_id}.zip"
    zip_path = get_downloads_dir() / zip_name

    with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
        for video_file in task_dir.glob("*.mp4"):
            zf.write(str(video_file), video_file.name)

    return {
        "success": True,
        "download_url": f"/api/download-zip/{zip_name}",
        "filename": zip_name,
    }


@app.get("/api/download-zip/{filename}")
async def download_zip(filename: str):
    """Serve a ZIP file."""
    safe_filename = Path(filename).name
    file_path = get_downloads_dir() / safe_filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="ZIP file not found.")

    return FileResponse(
        path=str(file_path),
        filename=safe_filename,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
    )


# ---------------------------------------------------------------------------
# YouTube Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/youtube/info")
async def youtube_info(req: YouTubeInfoRequest):
    """Fetch available video qualities for a YouTube URL."""
    url = req.url.strip()
    if not is_youtube_url(url):
        raise HTTPException(status_code=400, detail="Invalid YouTube URL. Please provide a valid YouTube video link.")

    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(_thread_pool, partial(_fetch_youtube_info, url)),
            timeout=30.0
        )
        return result
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Timed out while fetching video info. Please try again.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch video info: {e}")


def _fetch_youtube_info(url: str) -> dict:
    """Blocking — extract available formats from a YouTube video."""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'skip_download': True,
        'extractor_args': {'youtube': {'player_client': ['all']}},
        'js_runtimes': {'node': {}},
        'remote_components': ['ejs:github'],
        'cookiefile': get_cookies_path(),
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise HTTPException(status_code=404, detail="Could not find video information.")

    title = info.get('title', 'YouTube Video')
    thumbnail = info.get('thumbnail', '')
    duration = info.get('duration', 0)
    uploader = info.get('uploader', '')
    view_count = info.get('view_count', 0)

    # Collect unique quality options — ALWAYS prefer MP4 over WEBM
    formats = info.get('formats', [])
    quality_map = {}  # height -> format info

    def _format_score(ext: str, has_audio: bool) -> int:
        """Higher score = more preferred. MP4 > WEBM, combined > video-only."""
        score = 0
        if ext == 'mp4':
            score += 10
        if has_audio:
            score += 5
        return score

    for f in formats:
        height = f.get('height')
        vcodec = f.get('vcodec', 'none')
        acodec = f.get('acodec', 'none')
        ext = f.get('ext', '')
        format_id = f.get('format_id', '')
        filesize = f.get('filesize') or f.get('filesize_approx') or 0

        if not height or vcodec == 'none':
            continue

        # Determine quality label
        if height >= 2160:
            label = '4K (2160p)'
        elif height >= 1440:
            label = '2K (1440p)'
        elif height >= 1080:
            label = 'Full HD (1080p)'
        elif height >= 720:
            label = 'HD (720p)'
        elif height >= 480:
            label = '480p'
        elif height >= 360:
            label = '360p'
        elif height >= 240:
            label = '240p'
        elif height >= 144:
            label = '144p'
        else:
            continue

        has_audio = acodec != 'none'
        new_score = _format_score(ext, has_audio)

        existing = quality_map.get(height)
        if not existing:
            quality_map[height] = {
                'format_id': format_id,
                'height': height,
                'label': label,
                'ext': ext,
                'filesize': filesize,
                'has_audio': has_audio,
                'combined': has_audio,
                '_score': new_score,
            }
        else:
            old_score = existing.get('_score', 0)
            # Replace if new format has a better score, or same score but larger file
            if new_score > old_score or (new_score == old_score and filesize > existing['filesize']):
                quality_map[height] = {
                    'format_id': format_id,
                    'height': height,
                    'label': label,
                    'ext': ext,
                    'filesize': filesize,
                    'has_audio': has_audio,
                    'combined': has_audio,
                    '_score': new_score,
                }

    # Since we always merge to MP4 on download, override displayed ext to mp4
    for q in quality_map.values():
        q['ext'] = 'mp4'
        q.pop('_score', None)

    # Sort from highest to lowest quality
    qualities = sorted(quality_map.values(), key=lambda x: x['height'], reverse=True)

    # Format file sizes
    for q in qualities:
        if q['filesize'] > 0:
            if q['filesize'] >= 1_073_741_824:
                q['size_label'] = f"{q['filesize'] / 1_073_741_824:.1f} GB"
            elif q['filesize'] >= 1_048_576:
                q['size_label'] = f"{q['filesize'] / 1_048_576:.0f} MB"
            elif q['filesize'] >= 1024:
                q['size_label'] = f"{q['filesize'] / 1024:.0f} KB"
            else:
                q['size_label'] = f"{q['filesize']} B"
        else:
            q['size_label'] = ''

    # Also add audio-only option
    best_audio = None
    for f in formats:
        acodec = f.get('acodec', 'none')
        vcodec = f.get('vcodec', 'none')
        if acodec != 'none' and vcodec == 'none':
            abr = f.get('abr', 0) or 0
            if not best_audio or abr > (best_audio.get('abr', 0) or 0):
                best_audio = f

    if best_audio:
        fs = best_audio.get('filesize') or best_audio.get('filesize_approx') or 0
        if fs >= 1_048_576:
            size_label = f"{fs / 1_048_576:.0f} MB"
        elif fs >= 1024:
            size_label = f"{fs / 1024:.0f} KB"
        else:
            size_label = ''
        qualities.append({
            'format_id': best_audio['format_id'],
            'height': 0,
            'label': 'Audio Only (MP3)',
            'ext': best_audio.get('ext', 'm4a'),
            'filesize': fs,
            'size_label': size_label,
            'has_audio': True,
            'combined': False,
        })

    return {
        'success': True,
        'title': title,
        'thumbnail': thumbnail,
        'duration': duration,
        'uploader': uploader,
        'view_count': view_count,
        'qualities': qualities,
    }


@app.post("/api/youtube/download")
async def youtube_download(req: YouTubeDownloadRequest):
    """Download a YouTube video in the selected quality."""
    url = req.url.strip()
    if not is_youtube_url(url):
        raise HTTPException(status_code=400, detail="Invalid YouTube URL.")

    task_id = uuid.uuid4().hex[:12]
    task_dir = get_downloads_dir() / task_id
    task_dir.mkdir(exist_ok=True)

    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                _thread_pool,
                partial(_do_youtube_download, url, req.format_id, req.quality_label, task_id, task_dir)
            ),
            timeout=300.0  # 5 minutes for large 4K downloads
        )
        return result
    except asyncio.TimeoutError:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise HTTPException(status_code=504, detail="Download timed out (5 min). The video may be too large. Try a lower quality.")
    except HTTPException:
        raise
    except Exception as e:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))


def _do_youtube_download(url: str, format_id: str, quality_label: str, task_id: str, task_dir: Path) -> dict:
    """Blocking — download YouTube video via yt-dlp."""
    is_audio_only = quality_label.lower().startswith('audio')

    base_ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'extractor_args': {'youtube': {'player_client': ['all']}},
        'js_runtimes': {'node': {}},
        'remote_components': ['ejs:github'],
        'cookiefile': get_cookies_path(),
    }

    if is_audio_only:
        ydl_opts = {
            **base_ydl_opts,
            'format': f'{format_id}/bestaudio',
            'outtmpl': str(task_dir / '%(title)s.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            }],
        }
    else:
        # For video-only formats, merge with best audio
        ydl_opts = {
            **base_ydl_opts,
            'format': f'{format_id}+bestaudio/best',
            'outtmpl': str(task_dir / '%(title)s.%(ext)s'),
            'merge_output_format': 'mp4',
        }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=500, detail=f"YouTube download error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Download failed: {e}")

    if not info:
        raise HTTPException(status_code=500, detail="Download failed — no info returned.")

    # Find the downloaded file
    downloaded_file = None
    for f in task_dir.iterdir():
        if f.is_file() and f.suffix.lower() in {".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".ogg", ".opus", ".mov"}:
            downloaded_file = f
            break

    if not downloaded_file:
        raise HTTPException(status_code=500, detail="Download completed but no file was found.")

    return {
        'success': True,
        'video': {
            'filename': downloaded_file.name,
            'download_url': f'/api/download/{task_id}/{downloaded_file.name}',
            'title': info.get('title', ''),
            'quality': quality_label,
            'duration': info.get('duration', 0),
            'uploader': info.get('uploader', ''),
            'thumbnail': info.get('thumbnail', ''),
        },
    }


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
