# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic
#
# Download chain (in order of priority):
#   1. Cookies Base64  (COOKIES_DATA env var → yt-dlp with cookie_0.txt)
#   2. Railway YT API  (RAILWAY_YT_API_URL / RAILWAY_YT_API_KEY)
#   3. Shruti API      (SHRUTI_API_URL / SHRUTI_API_KEY)
#   4. xBit API        (YTPROXY_URL / YT_API_KEY)
#
# Stream URL chain (for instant playback without download):
#   1. yt-dlp --get-url  (uses cookies base64 if available)
#   2. Railway API       (validated with HEAD request before returning)

import asyncio
import glob
import os
import random
import re
import time as _time
from typing import Union

import aiohttp
import yt_dlp
from py_yt import Playlist, VideosSearch
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message

from ishu import config, logger
from ishu.helpers import utils

# ── Config ────────────────────────────────────────────────────────────────────
SHRUTI_API_URL      = getattr(config, "SHRUTI_API_URL",      "https://api.shrutibots.site")
SHRUTI_API_KEY      = getattr(config, "SHRUTI_API_KEY",      None)

RAILWAY_YT_API_URL  = getattr(config, "RAILWAY_YT_API_URL",  None)
RAILWAY_YT_API_KEY  = getattr(config, "RAILWAY_YT_API_KEY",  None)

YTPROXY_URL         = getattr(config, "YTPROXY_URL",         None)
YT_API_KEY          = getattr(config, "YT_API_KEY",          None)

DOWNLOAD_DIR        = "downloads"


# ── Cookie helper ─────────────────────────────────────────────────────────────
def cookie_txt_file() -> str | None:
    """Return a random cookie .txt file path from the cookies/ folder."""
    try:
        base_dir  = os.path.dirname(os.path.abspath(__file__))
        folder    = os.path.abspath(os.path.join(base_dir, "..", "cookies"))
        txt_files = glob.glob(os.path.join(folder, "*.txt"))
        if not txt_files:
            return None
        chosen   = random.choice(txt_files)
        log_file = os.path.join(folder, "logs.csv")
        with open(log_file, "a") as f:
            f.write(f"Chosen: {chosen}\n")
        return chosen
    except Exception:
        return None


# ── Link helpers ──────────────────────────────────────────────────────────────
def _normalize_youtube_link(
    link: str,
    base: str = "https://www.youtube.com/watch?v=",
) -> str:
    if not link:
        return ""
    cleaned = link.strip()
    if "youtube.com" not in cleaned and "youtu.be" not in cleaned:
        cleaned = base + cleaned
    cleaned = cleaned.split("&si=")[0].split("?si=")[0]
    if "&" in cleaned and "list=" not in cleaned:
        cleaned = cleaned.split("&")[0]
    return cleaned


def _extract_video_id(link: str) -> str | None:
    cleaned = _normalize_youtube_link(link)
    if not cleaned:
        return None
    if "v=" in cleaned:
        return cleaned.split("v=")[-1].split("&")[0]
    if "youtu.be/" in cleaned:
        return cleaned.split("youtu.be/")[-1].split("?")[0].split("&")[0]
    return cleaned if len(cleaned) == 11 else None


# ── Downloader 1: Cookies Base64 via yt-dlp ──────────────────────────────────
async def _cookies_download(link: str, media_type: str) -> str | None:
    """
    Priority 1: Download via yt-dlp using Base64 cookies (COOKIES_DATA env var).
    The YouTube class __init__ decodes COOKIES_DATA into cookies/cookie_0.txt.
    Falls back to any available cookie file in the cookies/ directory.
    Returns local file path on success, None on failure.
    """
    video_id  = _extract_video_id(link) or link
    ext       = "mp4" if media_type == "video" else "mp3"
    file_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        return file_path

    cookie = cookie_txt_file()
    # Only proceed if we actually have cookies; no-cookie yt-dlp is priority 4
    if not cookie:
        return None

    try:
        if media_type == "video":
            ydl_opts = {
                "format":              "bestvideo[height<=720]+bestaudio/best[height<=720]",
                "outtmpl":             file_path,
                "quiet":               True,
                "no_warnings":         True,
                "cookiefile":          cookie,
                "merge_output_format": "mp4",
            }
        else:
            ydl_opts = {
                "format":       "bestaudio/best",
                "outtmpl":      file_path,
                "quiet":        True,
                "no_warnings":  True,
                "cookiefile":   cookie,
                "postprocessors": [{
                    "key":              "FFmpegExtractAudio",
                    "preferredcodec":   "mp3",
                    "preferredquality": "192",
                }],
            }

        loop = asyncio.get_event_loop()
        def _run():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([_normalize_youtube_link(link)])

        await loop.run_in_executor(None, _run)

        candidates = [
            file_path,
            file_path.replace(f".{ext}", f".{ext}.{ext}"),
        ]
        for c in candidates:
            if os.path.exists(c) and os.path.getsize(c) > 0:
                logger.info("Cookies Base64 (yt-dlp) ✓ %s → %s", video_id, c)
                return c

        return None

    except Exception as exc:
        logger.warning("Cookies Base64 download failed for %s: %s", video_id, exc)
        return None


# ── Downloader 2: Railway YT API ─────────────────────────────────────────────
async def _railway_download(video_id: str, media_type: str) -> str | None:
    """
    Priority 2: Download via Railway self-hosted YouTube API proxy.
    Streams the media directly from the Railway endpoint to a local file.
    Returns local file path on success, None on failure.
    """
    if not RAILWAY_YT_API_URL or not RAILWAY_YT_API_KEY:
        return None

    ext        = "mp4" if media_type == "video" else "mp3"
    timeout_dl = 600   if media_type == "video" else 300
    file_path  = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        return file_path

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "X-API-Key": str(RAILWAY_YT_API_KEY),
    }
    endpoints = ["play/video/hq", "play/video"] if media_type == "video" else ["play/audio"]

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            for endpoint in endpoints:
                media_url = f"{RAILWAY_YT_API_URL}/{endpoint}?id={video_id}"

                async with session.get(
                    media_url,
                    timeout=aiohttp.ClientTimeout(total=timeout_dl),
                    allow_redirects=True,
                ) as file_resp:
                    if file_resp.status != 200:
                        logger.warning(
                            "Railway YT API stream failed: status %s for %s",
                            file_resp.status, endpoint,
                        )
                        continue

                    with open(file_path, "wb") as fobj:
                        async for chunk in file_resp.content.iter_chunked(1024 * 1024):
                            fobj.write(chunk)

                    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                        logger.info("Railway YT API ✓ %s → %s", video_id, file_path)
                        return file_path

            return None

    except Exception as exc:
        logger.warning("Railway YT API download failed for %s: %s", video_id, exc)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass
        return None


# ── Downloader 3: Shruti API ──────────────────────────────────────────────────
async def _shruti_download(video_id: str, media_type: str) -> str | None:
    """
    Priority 3: Download via Shruti API.
    GET {SHRUTI_API_URL}/download?url=<video_id>&type=audio|video&api_key=<key>
    Returns local file path on success, None on failure.
    """
    if not SHRUTI_API_KEY:
        return None

    ext         = "mp4" if media_type == "video" else "mp3"
    timeout_dl  = 600   if media_type == "video" else 300
    file_path   = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        return file_path

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{SHRUTI_API_URL}/download",
                params={"url": video_id, "type": media_type, "api_key": SHRUTI_API_KEY},
                timeout=aiohttp.ClientTimeout(total=timeout_dl),
            ) as resp:
                if resp.status != 200:
                    logger.warning("Shruti API status %s for %s", resp.status, video_id)
                    return None
                with open(file_path, "wb") as fobj:
                    async for chunk in resp.content.iter_chunked(131072):
                        fobj.write(chunk)

        if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
            logger.info("Shruti API ✓ %s → %s", video_id, file_path)
            return file_path

        return None

    except Exception as exc:
        logger.warning("Shruti API error for %s: %s", video_id, exc)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass
        return None


# ── Downloader 4: xBit API ────────────────────────────────────────────────────
async def _xbit_download(link: str, media_type: str) -> str | None:
    """
    Priority 4: Download via xBit / YTPROXY API.
    GET {YTPROXY_URL}/info/<video_id>  →  audio_url / video_url  →  stream download.
    Returns local file path on success, None on failure.
    """
    if not YTPROXY_URL or not YT_API_KEY:
        return None

    video_id = _extract_video_id(link)
    if not video_id or len(video_id) < 3:
        return None

    ext         = "mp4" if media_type == "video" else "mp3"
    timeout_dl  = 600   if media_type == "video" else 300
    file_path   = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        return file_path

    headers = {
        "x-api-key": str(YT_API_KEY),
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(
                f"{YTPROXY_URL}/info/{video_id}",
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    logger.warning("xBit info failed: status %s", resp.status)
                    return None
                try:
                    data = await resp.json(content_type=None)
                except Exception as e:
                    logger.warning("xBit info returned invalid JSON: %s", e)
                    return None

            if data.get("status") != "success":
                logger.warning("xBit API error: %s", data.get("message", "unknown"))
                return None

            media_url = (
                data.get("video_url") if media_type == "video" else data.get("audio_url")
            )
            if not media_url:
                logger.warning("xBit: no %s_url in response", media_type)
                return None

            async with session.get(
                media_url,
                timeout=aiohttp.ClientTimeout(total=timeout_dl),
                allow_redirects=True,
            ) as file_resp:
                if file_resp.status != 200:
                    return None
                with open(file_path, "wb") as fobj:
                    async for chunk in file_resp.content.iter_chunked(1024 * 1024):
                        fobj.write(chunk)

        if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
            logger.info("xBit API ✓ %s → %s", video_id, file_path)
            return file_path

        return None

    except Exception as exc:
        logger.warning("xBit download failed for %s: %s", video_id, exc)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass
        return None


# ── Downloader 5: yt-dlp without cookies (last resort) ───────────────────────
async def _ytdlp_nocookie_download(link: str, media_type: str) -> str | None:
    """
    Last-resort local yt-dlp download without any cookies.
    Used only when no cookie file is available.
    Returns local file path or None.
    """
    video_id  = _extract_video_id(link) or link
    ext       = "mp4" if media_type == "video" else "mp3"
    file_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        return file_path

    try:
        if media_type == "video":
            ydl_opts = {
                "format":              "bestvideo[height<=720]+bestaudio/best[height<=720]",
                "outtmpl":             file_path,
                "quiet":               True,
                "no_warnings":         True,
                "merge_output_format": "mp4",
            }
        else:
            ydl_opts = {
                "format":       "bestaudio/best",
                "outtmpl":      file_path,
                "quiet":        True,
                "no_warnings":  True,
                "postprocessors": [{
                    "key":              "FFmpegExtractAudio",
                    "preferredcodec":   "mp3",
                    "preferredquality": "192",
                }],
            }

        loop = asyncio.get_event_loop()
        def _run():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([_normalize_youtube_link(link)])

        await loop.run_in_executor(None, _run)

        candidates = [
            file_path,
            file_path.replace(f".{ext}", f".{ext}.{ext}"),
        ]
        for c in candidates:
            if os.path.exists(c) and os.path.getsize(c) > 0:
                logger.info("yt-dlp (no-cookie) ✓ %s → %s", video_id, c)
                return c

        return None

    except Exception as exc:
        logger.warning("yt-dlp (no-cookie) download failed for %s: %s", video_id, exc)
        return None


# ── Main download entrypoint ──────────────────────────────────────────────────
async def _download_with_fallback(
    link: str,
    media_type: str,
) -> tuple[str | None, str]:
    """
    Try all downloaders in priority order:
      1. Cookies Base64 (yt-dlp + COOKIES_DATA)
      2. Railway YT API
      3. Shruti API
      4. xBit API
      5. yt-dlp without cookies (absolute last resort)
    Returns (file_path, downloader_name)
    """
    video_id = _extract_video_id(link) or link

    # 1. Cookies Base64 (yt-dlp with decoded COOKIES_DATA)
    result = await _cookies_download(link, media_type)
    if result:
        return result, "cookies_b64"

    # 2. Railway YT API
    result = await _railway_download(video_id, media_type)
    if result:
        return result, "railway"

    # 3. Shruti API
    result = await _shruti_download(video_id, media_type)
    if result:
        return result, "shruti"

    # 4. xBit API
    result = await _xbit_download(link, media_type)
    if result:
        return result, "xbit"

    # 5. yt-dlp without cookies (last resort)
    result = await _ytdlp_nocookie_download(link, media_type)
    if result:
        return result, "ytdlp"

    logger.error("All download methods failed for: %s", video_id)
    return None, "none"


# ── Public helpers (kept for backward compat with play.py / calls.py) ─────────
async def download_song(link: str, title: str | None = None) -> str | None:
    path, _ = await _download_with_fallback(link, "audio")
    return path


async def download_video(link: str, title: str | None = None) -> str | None:
    path, _ = await _download_with_fallback(link, "video")
    return path


# ── YouTube class ─────────────────────────────────────────────────────────────
class YouTube:
    def __init__(self):
        self.base     = "https://www.youtube.com/watch?v="
        self.regex    = r"(?:youtube\.com|youtu\.be)"
        self.listbase = "https://youtube.com/playlist?list="
        self.reg      = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
        self.api      = None
        self.cookies_dir = os.path.join(os.path.dirname(__file__), "..", "cookies")

        # Decode COOKIES_DATA (base64 or raw Netscape) env var → cookie_0.txt for yt-dlp use
        cookies_data = getattr(config, "COOKIES_DATA", None) or os.environ.get("COOKIES_DATA")
        if cookies_data:
            cookies_data = cookies_data.strip()
            decoded = None
            # Try base64 decoding first
            try:
                import base64
                decoded = base64.b64decode(cookies_data).decode("utf-8")
                # Simple check to see if it looks like a netscape cookie file
                if "# Netscape" not in decoded and "\t" not in decoded:
                    # If decoded looks like garbage, maybe the original data was raw text
                    decoded = None
            except Exception:
                decoded = None

            if not decoded:
                # Fall back to raw text if it looks like cookies format
                if "# Netscape" in cookies_data or "\t" in cookies_data:
                    decoded = cookies_data

            if decoded:
                try:
                    os.makedirs(self.cookies_dir, exist_ok=True)
                    with open(os.path.join(self.cookies_dir, "cookie_0.txt"), "w") as f:
                        f.write(decoded)
                    logger.info("Loaded cookies from COOKIES_DATA.")
                except Exception as e:
                    logger.error("Error writing cookies file: %s", e)
            else:
                logger.error("COOKIES_DATA provided but could not be parsed as Base64 or raw Netscape cookies.")

        self.dl_stats = {
            "total_requests": 0,
            "cookies_b64":    0,
            "railway":        0,
            "shruti":         0,
            "xbit":           0,
            "ytdlp":          0,
            "existing_files": 0,
            "failed":         0,
        }

    # ── Validators ────────────────────────────────────────────────────────────
    def valid(self, url: str) -> bool:
        return bool(re.search(self.regex, url))

    def invalid(self, url: str) -> bool:
        return not self.valid(url)

    # ── Cookie management ─────────────────────────────────────────────────────
    async def save_cookies(self, urls: list) -> None:
        if not urls:
            return
        os.makedirs(self.cookies_dir, exist_ok=True)
        try:
            async with aiohttp.ClientSession() as session:
                for i, url in enumerate(urls):
                    if not url:
                        continue
                    try:
                        async with session.get(
                            url, timeout=aiohttp.ClientTimeout(total=15)
                        ) as resp:
                            if resp.status == 200:
                                content = await resp.text()
                                path = os.path.join(self.cookies_dir, f"cookies_{i}.txt")
                                with open(path, "w") as f:
                                    f.write(content)
                                logger.info("Saved cookies → %s", path)
                            else:
                                logger.warning("Cookie fetch failed %s (status %s)", url, resp.status)
                    except Exception as e:
                        logger.warning("Cookie error from %s: %s", url, e)
        except Exception as e:
            logger.warning("save_cookies error: %s", e)

    # ── URL utilities ─────────────────────────────────────────────────────────
    async def exists(self, link: str, videoid: Union[bool, str] = None) -> bool:
        if videoid:
            link = self.base + link
        return bool(re.search(self.regex, link))

    async def url(self, message_1: Message) -> Union[str, None]:
        messages = [message_1]
        if message_1.reply_to_message:
            messages.append(message_1.reply_to_message)
        for message in messages:
            text = message.text or message.caption or ""
            if message.entities:
                for entity in message.entities:
                    if entity.type == MessageEntityType.URL:
                        return text[entity.offset: entity.offset + entity.length]
                    if entity.type == MessageEntityType.TEXT_LINK:
                        return entity.url
            if message.caption_entities:
                for entity in message.caption_entities:
                    if entity.type == MessageEntityType.TEXT_LINK:
                        return entity.url
        return None

    # ── Metadata fetchers ─────────────────────────────────────────────────────
    async def details(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        link = _normalize_youtube_link(link)
        results = VideosSearch(link, limit=1)
        r = (await results.next())["result"][0]
        title        = r["title"]
        duration_min = r["duration"]
        thumbnail    = r["thumbnails"][0]["url"].split("?")[0]
        vidid        = r["id"]
        duration_sec = int(utils.to_seconds(duration_min)) if duration_min else 0
        return title, duration_min, duration_sec, thumbnail, vidid

    async def title(self, link: str, videoid: Union[bool, str] = None) -> str | None:
        if videoid:
            link = self.base + link
        link = _normalize_youtube_link(link)
        results = VideosSearch(link, limit=1)
        for r in (await results.next())["result"]:
            return r["title"]
        return None

    async def duration(self, link: str, videoid: Union[bool, str] = None) -> str | None:
        if videoid:
            link = self.base + link
        link = _normalize_youtube_link(link)
        results = VideosSearch(link, limit=1)
        for r in (await results.next())["result"]:
            return r["duration"]
        return None

    async def thumbnail(self, link: str, videoid: Union[bool, str] = None) -> str | None:
        if videoid:
            link = self.base + link
        link = _normalize_youtube_link(link)
        results = VideosSearch(link, limit=1)
        for r in (await results.next())["result"]:
            return r["thumbnails"][0]["url"].split("?")[0]
        return None

    async def track(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        link = _normalize_youtube_link(link)
        results = VideosSearch(link, limit=1)
        for r in (await results.next())["result"]:
            track_details = {
                "title":        r["title"],
                "link":         r["link"],
                "vidid":        r["id"],
                "duration_min": r["duration"],
                "thumb":        r["thumbnails"][0]["url"].split("?")[0],
            }
            return track_details, r["id"]
        return None, None

    async def search(
        self,
        query: str,
        message_id: int,
        video: bool = False,
    ):
        """Search YouTube and return a Track dataclass or None.
        Prioritizes official studio versions, avoids remixes/covers/live etc.
        """
        from ishu.helpers._dataclass import Track

        avoid_keywords = [
            "remix", "cover", "live", "slowed", "reverb", "extended", "acoustic",
            "instrumental", "karaoke", "8d", "bass boosted", "nightcore", "edit"
        ]

        query_lower = query.strip().lower()
        explicit_avoid = any(kw in query_lower for kw in avoid_keywords)

        try:
            search_queries = [
                f"{query.strip()} official audio",
                f"{query.strip()} official video",
                query.strip()
            ] if not explicit_avoid else [query.strip()]

            for sq in search_queries:
                results = VideosSearch(sq, limit=10)
                raw_results = (await results.next())["result"]
                if not raw_results:
                    continue

                filtered = []
                for r in raw_results:
                    title_lower = r.get("title", "").lower()

                    if not explicit_avoid:
                        if any(kw in title_lower for kw in avoid_keywords):
                            continue

                    duration_str = r.get("duration") or "0:00"
                    parts = duration_str.split(":")
                    try:
                        if len(parts) == 3:
                            secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                        elif len(parts) == 2:
                            secs = int(parts[0]) * 60 + int(parts[1])
                        else:
                            secs = 0
                    except (ValueError, IndexError):
                        secs = 0

                    if 30 <= secs <= 3600:
                        filtered.append(r)

                if filtered:
                    r = filtered[0]
                    vidid = r["id"]
                    duration_min = r.get("duration") or "00:00"
                    duration_sec = int(utils.to_seconds(duration_min)) if duration_min else 0
                    view_count = None
                    if "viewCount" in r and isinstance(r["viewCount"], dict):
                        view_count = r["viewCount"].get("short") or r["viewCount"].get("text")
                    return Track(
                        id           = vidid,
                        title        = r["title"],
                        url          = r.get("link", self.base + vidid),
                        duration     = duration_min,
                        duration_sec = duration_sec,
                        thumbnail    = r["thumbnails"][0]["url"].split("?")[0],
                        channel_name = (r.get("channel") or {}).get("name", ""),
                        message_id   = message_id,
                        video        = video,
                        time         = int(_time.time()),
                        view_count   = view_count,
                    )

            return None
        except Exception as e:
            logger.warning("YouTube search error for '%s': %s", query, e)
            return None

    # ── Slider ────────────────────────────────────────────────────────────────
    async def slider(self, link: str, query_type: int, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        link        = _normalize_youtube_link(link)
        search      = VideosSearch(link, limit=10)
        raw_results = (await search.next()).get("result", [])

        filtered = []
        for item in raw_results:
            duration_str = item.get("duration") or "0:00"
            parts = duration_str.split(":")
            try:
                if len(parts) == 3:
                    secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                elif len(parts) == 2:
                    secs = int(parts[0]) * 60 + int(parts[1])
                else:
                    secs = 0
            except (ValueError, IndexError):
                continue
            if 0 < secs <= 3600:
                filtered.append(item)

        if not filtered or query_type >= len(filtered):
            raise ValueError("No suitable videos found within duration limit")

        s = filtered[query_type]
        return s["title"], s.get("duration") or "0:00", s["thumbnails"][0]["url"].split("?")[0], s["id"]

    # ── Formats (yt-dlp) ──────────────────────────────────────────────────────
    async def formats(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        link = _normalize_youtube_link(link)
        ydl = yt_dlp.YoutubeDL({"quiet": True})
        with ydl:
            info = ydl.extract_info(link, download=False)
        formats_available = []
        for fmt in info.get("formats", []):
            try:
                if "dash" not in str(fmt["format"]).lower():
                    formats_available.append({
                        "format":      fmt["format"],
                        "filesize":    fmt.get("filesize"),
                        "format_id":   fmt["format_id"],
                        "ext":         fmt["ext"],
                        "format_note": fmt.get("format_note"),
                        "yturl":       link,
                    })
            except Exception:
                continue
        return formats_available, link

    # ── Video stream URL (yt-dlp, no download) ────────────────────────────────
    async def video(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        link = _normalize_youtube_link(link)
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "-g", "-f", "best[height<=?720][width<=?1280]", link,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if stdout:
            return 1, stdout.decode().split("\n")[0]
        return 0, stderr.decode()

    async def get_stream_url(
        self,
        video_id: str,
        video: bool = False,
    ) -> str | None:
        """
        Get a direct stream URL for instant playback (no download).

        Method 1 — Cookies Base64 (yt-dlp extract_info):
          Uses COOKIES_DATA-decoded cookie file for authenticated access.
          Returns a direct googlevideo.com URL valid for ~6 hours.

        Method 2 — Railway API:
          Returns the Railway proxy endpoint URL and validates it with
          a HEAD request before returning to avoid silent failures.
        """
        link = _normalize_youtube_link(video_id, self.base)
        cookie = cookie_txt_file()

        # ── Method 1: yt-dlp with cookies base64 ─────────────────────────────
        if cookie:
            try:
                ydl_opts = {
                    "format": (
                        "bestvideo[height<=720]+bestaudio/best[height<=720]"
                        if video else "bestaudio/best"
                    ),
                    "quiet":       True,
                    "no_warnings": True,
                    "cookiefile":  cookie,
                }
                loop = asyncio.get_event_loop()
                def _run():
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(link, download=False)
                        # For merged formats, prefer the best audio URL
                        if info.get("url"):
                            return info["url"]
                        formats = info.get("formats") or []
                        if formats:
                            return formats[-1].get("url")
                        return None

                url = await asyncio.wait_for(
                    loop.run_in_executor(None, _run),
                    timeout=30,
                )
                if url:
                    logger.info("Stream URL via Cookies Base64: %s", video_id)
                    return url
            except asyncio.TimeoutError:
                logger.warning("get_stream_url yt-dlp (cookies) timed out for %s", video_id)
            except Exception as e:
                logger.warning("get_stream_url yt-dlp (cookies) failed for %s: %s", video_id, e)

        # ── Method 2: Railway API (validated) ────────────────────────────────
        if RAILWAY_YT_API_URL and RAILWAY_YT_API_KEY:
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "X-API-Key": str(RAILWAY_YT_API_KEY),
                }
                endpoint  = "play/video/hq" if video else "play/audio"
                media_url = f"{RAILWAY_YT_API_URL}/{endpoint}?id={video_id}"

                # Validate the endpoint responds before returning it as stream URL
                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.head(
                        media_url,
                        timeout=aiohttp.ClientTimeout(total=10),
                        allow_redirects=True,
                    ) as resp:
                        if resp.status in (200, 206):
                            logger.info("Stream URL via Railway API: %s", video_id)
                            return media_url
                        else:
                            logger.warning(
                                "Railway stream URL validation failed: status %s for %s",
                                resp.status, media_url
                            )
            except Exception as e:
                logger.warning("Railway get_stream_url failed: %s", e)

        # ── Method 3: yt-dlp without cookies (last resort fallback) ──────────
        # Only try if we haven't succeeded yet and didn't use cookies
        if not cookie:
            try:
                ydl_opts = {
                    "format": (
                        "bestvideo[height<=720]+bestaudio/best[height<=720]"
                        if video else "bestaudio/best"
                    ),
                    "quiet":       True,
                    "no_warnings": True,
                }
                loop = asyncio.get_event_loop()
                def _run():
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(link, download=False)
                        if info.get("url"):
                            return info["url"]
                        formats = info.get("formats") or []
                        if formats:
                            return formats[-1].get("url")
                        return None

                url = await asyncio.wait_for(
                    loop.run_in_executor(None, _run),
                    timeout=30,
                )
                if url:
                    logger.info("Stream URL via yt-dlp (no cookies): %s", video_id)
                    return url
            except asyncio.TimeoutError:
                logger.warning("get_stream_url yt-dlp (no cookies) timed out for %s", video_id)
            except Exception as e:
                logger.warning("get_stream_url yt-dlp (no cookies) failed for %s: %s", video_id, e)

        return None

    # ── Download (main method called by play.py / calls.py) ──────────────────
    async def download(
        self,
        video_id: str,
        video: bool = False,
        title: str | None = None,
    ) -> str | None:
        """
        Download audio/video by video_id using the full fallback chain:
          1. Cookies Base64 (yt-dlp + COOKIES_DATA)
          2. Railway YT API
          3. Shruti API
          4. xBit API
          5. yt-dlp without cookies
        Returns file path or None.
        """
        self.dl_stats["total_requests"] += 1
        link = _normalize_youtube_link(video_id, self.base)

        try:
            result, downloader = await _download_with_fallback(
                link, "video" if video else "audio"
            )
            if result:
                self.dl_stats[downloader] = self.dl_stats.get(downloader, 0) + 1
                logger.info(
                    "YouTube.download success: %s (%s) via %s",
                    video_id,
                    "video" if video else "audio",
                    downloader,
                )
            else:
                self.dl_stats["failed"] += 1
            return result
        except Exception as e:
            self.dl_stats["failed"] += 1
            logger.warning("YouTube.download error for '%s': %s", video_id, e)
            return None

    # ── Playlist ──────────────────────────────────────────────────────────────
    async def playlist(
        self,
        limit: int,
        mention: str,
        link: str,
        video: bool = False,
    ) -> list:
        """Fetch playlist tracks, return list of Track dataclasses."""
        from ishu.helpers._dataclass import Track

        link = _normalize_youtube_link(link)
        try:
            plist = await Playlist.get(link)
        except Exception:
            return []

        tracks = []
        for data in (plist.get("videos") or [])[:limit]:
            if not data:
                continue
            vidid = data.get("id")
            if not vidid:
                continue
            duration_min = data.get("duration") or "00:00"
            duration_sec = int(utils.to_seconds(duration_min)) if duration_min else 0
            thumbs       = data.get("thumbnails") or []
            thumbnail    = thumbs[0].get("url", "").split("?")[0] if thumbs else ""
            view_count = None
            if "viewCount" in data and isinstance(data["viewCount"], dict):
                view_count = data["viewCount"].get("short") or data["viewCount"].get("text")
            channel_name = (data.get("channel") or {}).get("name", "")
            tracks.append(Track(
                id           = vidid,
                title        = data.get("title") or vidid,
                url          = data.get("link") or self.base + vidid,
                duration     = duration_min,
                duration_sec = duration_sec,
                thumbnail    = thumbnail,
                user         = mention,
                video        = video,
                time         = int(_time.time()),
                view_count   = view_count,
                channel_name = channel_name,
            ))
        return tracks
