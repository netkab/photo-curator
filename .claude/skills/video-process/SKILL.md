---
name: video-process
description: Compress videos to HD with good bitrate (ffmpeg h264_nvenc, GPU) and assemble highlight reels — detect scenes (PySceneDetect), score them with the local vision model, cluster clips by date/place/event, and concatenate the best moments. Outputs new derived files and reports size savings; review before keeping. Nothing replaces originals automatically.
---

Compress videos and build highlight reels. All outputs are **new** files under `data/derived/`;
originals are never replaced.

## Steps

1. Compress videos (targets 1080p with a quality floor; reports size saved):

   ```powershell
   cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli video --compress
   ```

2. Build highlight reels from analyzed clips (scene detection + vision scoring + clustering):

   ```powershell
   cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli video --highlights
   ```

3. Review on the **Videos** page: compare original vs compressed size/quality, and preview generated
   highlight reels. Approve → `upload`/`highlight` review-actions.

4. **Upload and retire from the extension's Video tab.** Approve & upload pushes the derived file to
   Google Photos, then per-source **Retire this** trashes the clips it replaces.

## Retiring source clips — the asymmetry that matters

| Kind | Relationship | Retiring sources |
|---|---|---|
| `compressed` | 1:1 replacement of one clip | Sensible — same content, smaller file |
| `highlight` | A montage cut from **many** clips | The reel is **not** a substitute for its sources |

`POST /api/videos/{derived_id}/retire-sources` therefore requires an **explicit** `media_ids` list —
there is no "retire all sources" shortcut, and the UI pre-selects nothing. That is also how the user
skips clips they want to keep. Guards, all tested:

- refuses unless the derived file's status is `uploaded` (retiring first leaves neither copy);
- rejects media that aren't sources of that derived file;
- rejects an empty selection;
- skips sources not live in Google Photos and reports them as `blocked`.

**Never suggest retiring a reel's sources wholesale.** If asked to "free space from videos", prefer
`compressed` outputs, or name the specific clips.

## Uploads require one-time OAuth setup

Without `PHOTOS_OAUTH_CLIENT_ID` / `_SECRET`, `uploader.enabled()` is False: approved files are
exported to a folder for manual upload and **retiring stays blocked**. The setup walkthrough is
inline in `backend/.env.example` (~5 min in the Google Cloud console, free, `appendonly` scope only).
Only the user can complete it — it needs their Google account and a browser consent click.

## Notes

- Compression: `h264_nvenc`, target ~8 Mbps @ 1080p (configurable: `VIDEO_TARGET_HEIGHT`,
  `VIDEO_BITRATE`). CPU fallback: `libx264 -crf 18..22`. HEVC is skipped (poor on Pascal NVENC).
- Highlights: PySceneDetect splits scenes; frames are sampled and scored by moondream; top scenes
  across clustered clips are concatenated with ffmpeg.
- GPU + vision passes are batch jobs — expect minutes per clip, not real-time.
- Video is the bulk of the library on disk (~103 GB of 157 GB here) and needs the **original files** —
  Google only serves downscaled thumbnails, so none of this can run from the live library. That is a
  large part of why the Takeout export is still required.
- `photo-reclaim` skips video duplicates by default: biggest files, hardest to re-acquire, and their
  "duplicates" are usually re-encodes rather than true copies.

## Rules

- Never overwrite or delete an original video. Compressed/highlight outputs go to `data/derived/`
  and require approval before upload.
