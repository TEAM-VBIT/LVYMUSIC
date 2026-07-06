# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic


import asyncio
from pathlib import Path

from pyrogram import filters, types

from ishu import anon, app, config, db, lang, logger, queue, tg, yt
from ishu.helpers import buttons, utils
from ishu.helpers._play import checkUB

# Track active background download tasks to avoid duplicates
_background_tasks = set()


async def _background_download_task(track) -> None:
    """Background task to download a queued track if not already downloaded."""
    try:
        if not track.file_path:
            fname = f"downloads/{track.id}.{'mp4' if track.video else 'webm'}"
            if not Path(fname).exists():
                track.file_path = await yt.download(track.id, video=track.video)
                logger.info("Background download complete: %s", track.id)
    except Exception as e:
        logger.warning("Background download failed for %s: %s", track.id, e)


def _start_background_download(track) -> None:
    """Start a background download task for a track if not already running."""
    if track.id not in _background_tasks:
        _background_tasks.add(track.id)
        task = asyncio.create_task(_background_download_task(track))
        task.add_done_callback(lambda _: _background_tasks.discard(track.id))


def playlist_to_queue(chat_id: int, tracks: list) -> str:
    text = "<blockquote expandable>"
    for track in tracks:
        pos = queue.add(chat_id, track)
        text += f"<b>{pos}.</b> {track.title}\n"
        _start_background_download(track)
    text = text[:1948] + "</blockquote>"
    return text


@app.on_message(
    filters.command(["play", "playforce", "vplay", "vplayforce"])
    & filters.group
    & ~app.bl_users
)
@lang.language()
@checkUB
async def play_hndlr(
    _,
    m: types.Message,
    force: bool = False,
    m3u8: bool = False,
    video: bool = False,
    url: str = None,
) -> None:
    sent = await m.reply_text(m.lang["play_searching"])
    file = None
    mention = m.from_user.mention
    media = tg.get_media(m.reply_to_message) if m.reply_to_message else None
    tracks = []

    if media:
        setattr(sent, "lang", m.lang)
        file = await tg.download(m.reply_to_message, sent)

    elif m3u8:
        file = await tg.process_m3u8(url, sent.id, video)

    elif url:
        if "playlist" in url:
            await sent.edit_text(m.lang["playlist_fetch"])
            tracks = await yt.playlist(
                config.PLAYLIST_LIMIT, mention, url, video
            )

            if not tracks:
                return await sent.edit_text(m.lang["playlist_error"])

            file = tracks[0]
            tracks.remove(file)
            file.message_id = sent.id
        else:
            file = await yt.search(url, sent.id, video=video)

        if not file:
            return await sent.edit_text(
                m.lang["play_not_found"].format(config.SUPPORT_CHAT)
            )

    elif len(m.command) >= 2:
        query = " ".join(m.command[1:])
        file = await yt.search(query, sent.id, video=video)
        if not file:
            return await sent.edit_text(
                m.lang["play_not_found"].format(config.SUPPORT_CHAT)
            )

    if not file:
        return await sent.edit_text(m.lang["play_usage"])

    if file.duration_sec > config.DURATION_LIMIT:
        return await sent.edit_text(
            m.lang["play_duration_limit"].format(config.DURATION_LIMIT // 60)
        )

    if await db.is_logger():
        await utils.play_log(m, sent.link, file.title, file.duration)

    file.user = mention

    if force:
        queue.force_add(m.chat.id, file)
    else:
        position = queue.add(m.chat.id, file)

        if position != 0 or await db.get_call(m.chat.id):
            title = file.title.split("|")[0].split("(")[0].strip()

            await sent.edit_text(
                m.lang["play_queued"].format(
                    position,
                    file.url,
                    title,
                    file.duration,
                    m.from_user.mention,
                ),
                reply_markup=buttons.play_queued(
                    m.chat.id, file.id, m.lang["play_now"]
                ),
            )
            
            # Start background download for the queued song
            _start_background_download(file)

            if tracks:
                added = playlist_to_queue(m.chat.id, tracks)
                await app.send_message(
                    chat_id=m.chat.id,
                    text=m.lang["playlist_queued"].format(len(tracks)) + added,
                )

            return

    # First try to get stream URL for instant playback
    if not file.stream_url and not file.file_path:
        fname = f"downloads/{file.id}.{'mp4' if video else 'webm'}"
        if Path(fname).exists():
            file.file_path = fname
        else:
            await sent.edit_text(m.lang["play_searching"])  # Show "searching" while getting stream
            file.stream_url = await yt.get_stream_url(file.id, video=video)

    # Start playback immediately with stream_url if available
    await anon.play_media(chat_id=m.chat.id, message=sent, media=file)
    
    # Start background download of this track (and playlist tracks) for future
    _start_background_download(file)
    if tracks:
        added = playlist_to_queue(m.chat.id, tracks)
        await app.send_message(
            chat_id=m.chat.id,
            text=m.lang["playlist_queued"].format(len(tracks)) + added,
        )
