# Third-party notices

This project's own code is MIT-licensed (see [LICENSE](LICENSE)). It vendors and depends on
components under their own licences, listed here.

## Vendored source

**`gp-extension/vendor/gptk/`** — [Google Photos Toolkit](https://github.com/xob0t/Google-Photos-Toolkit)
by **xob0t**, MIT License, Copyright (c) 2024 xob0t. Pinned at v3.2.0; full licence text in
`gp-extension/vendor/gptk/LICENSE`. It encodes the request shapes for Google Photos' undocumented
internal API — the part that makes trash/restore/album operations possible at all.

## Approach credit

The technique of driving Google Photos' internal `batchexecute` endpoint from a browser extension
(rather than the now-restricted official API) was pioneered by
[google-photos-deduper](https://github.com/mtalcott/google-photos-deduper) by **mtalcott**. No code
from that project is included here; this is a from-scratch implementation, built to add server-side
resumable batching, richer keeper selection, and Maps/video/enhance workflows.

## Runtime dependencies (not vendored — installed by `setup-models.ps1` / `pip`)

| Component | Licence | Used for |
|---|---|---|
| [OpenCLIP](https://github.com/mlfoundations/open_clip) | MIT | Near-duplicate photo detection |
| [InsightFace](https://github.com/deepinsight/insightface) | MIT / model-dependent | Face detection & clustering |
| [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) | BSD-3-Clause | Photo upscaling |
| [GFPGAN](https://github.com/TencentARC/GFPGAN) | Apache-2.0 | Face restoration |
| [Florence-2](https://huggingface.co/microsoft/Florence-2-large) | MIT | OCR / captioning |
| [Ollama](https://ollama.com) + moondream | MIT (Ollama) / model-dependent | Local vision captioning |
| [PySceneDetect](https://github.com/Breakthrough/PySceneDetect) | BSD-3-Clause | Video scene detection |
| [FFmpeg](https://ffmpeg.org) | LGPL/GPL (build-dependent) | Video transcode/compression |

Model weights are distributed by their respective authors under their own licences and are not
covered by this project's MIT licence. Check each project's licence before commercial use.
