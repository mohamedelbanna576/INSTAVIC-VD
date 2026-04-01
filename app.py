"""
INSTAVIC VD — Premium Instagram Downloader
FastAPI Backend Server
"""

import os
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
app = FastAPI(title="INSTAVIC VD", version="2.0.0")

BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
STATIC_DIR = BASE_DIR / "static"
CONFIG_FILE = BASE_DIR / "config.json"
PROXIES_FILE = BASE_DIR / "proxies.txt"

DOWNLOADS_DIR.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount(
    "/downloads",
    StaticFiles(directory=str(DOWNLOADS_DIR)),
    name="downloads",
)

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

if not CONFIG_FILE.exists():
    with open(CONFIG_FILE, "w") as f:
        json.dump({"instagram_session_id": ""}, f)

def load_config() -> dict:
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"instagram_session_id": ""}

def save_config(data: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f)

def get_random_proxy() -> Optional[str]:
    """Retrieve a random proxy line from proxies.txt if available."""
    if not PROXIES_FILE.exists():
        return None
    try:
        with open(PROXIES_FILE, "r") as f:
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
        "proxy_enabled": PROXIES_FILE.exists()
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
    task_dir = DOWNLOADS_DIR / task_id
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
        if 'rate' in msg or '429' in msg:
            raise HTTPException(status_code=429, detail="Instagram is rate-limiting this IP. Please click 'Connect Account' and provide your session ID to fix this.")
        elif 'private' in msg or 'login' in msg:
            raise HTTPException(status_code=403, detail="This post is from a private account.")
        elif 'not found' in msg or '404' in msg:
            raise HTTPException(status_code=404, detail="Post not found or was deleted.")
        else:
            raise HTTPException(status_code=500, detail=f"Download failed: {e}")
    except Exception as e:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Both download methods failed. Please connect your Instagram account. Error: {e}")


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
    task_dir = DOWNLOADS_DIR / task_id
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


@app.get("/api/download/{task_id}/{filename}")
async def download_file(task_id: str, filename: str):
    """Serve a downloaded video file."""
    # Sanitize to prevent path traversal
    safe_filename = Path(filename).name
    file_path = DOWNLOADS_DIR / task_id / safe_filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found.")

    return FileResponse(
        path=str(file_path),
        filename=safe_filename,
        media_type="video/mp4",
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
    )


@app.post("/api/bulk/zip/{task_id}")
async def create_zip(task_id: str):
    """Create a ZIP archive of all downloaded videos for a bulk task."""
    task = bulk_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    task_dir = DOWNLOADS_DIR / task_id
    if not task_dir.exists():
        raise HTTPException(status_code=404, detail="Download folder not found.")

    zip_name = f"{task.get('username', 'videos')}_{task_id}.zip"
    zip_path = DOWNLOADS_DIR / zip_name

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
    file_path = DOWNLOADS_DIR / safe_filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="ZIP file not found.")

    return FileResponse(
        path=str(file_path),
        filename=safe_filename,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
    )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
