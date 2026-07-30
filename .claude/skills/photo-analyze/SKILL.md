---
name: photo-analyze
description: Run the local AI analysis pass over un-analyzed catalog media — generate captions/tags (moondream), OCR text (Florence-2), detect and cluster faces (InsightFace), reverse-geocode GPS to place names, and classify portraits. Runs entirely on the local GPU. Use --batch for unattended overnight runs. Pass an optional --limit.
---

Run the local AI analysis pass on catalog items that haven't been analyzed yet. All inference is
local (GTX 1070, 8 GB, Pascal).

## Check progress first

```powershell
cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli status
```

Shows per-stage counts: blur, captions, OCR, faces, geo — and how many remain.

## Steps

1. **Single batch** (tune `--limit` to control GPU time / thermals):

   ```powershell
   cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli analyze --limit 200
   ```

2. **Unattended batch loop** (runs until all done, Ctrl+C safe — progress saved between batches):

   ```powershell
   cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli analyze --batch --limit 200
   ```

   Optional tuning:
   ```powershell
   # Smaller batches + longer cooldown if GPU runs hot
   .\.venv\Scripts\python -m app.cli analyze --batch --limit 100 --cooldown 60

   # Larger batches if thermals are fine
   .\.venv\Scripts\python -m app.cli analyze --batch --limit 500 --cooldown 10
   ```

3. **Single stage only** (useful for re-runs or debugging):

   ```powershell
   .\.venv\Scripts\python -m app.cli analyze --only captions --limit 50
   .\.venv\Scripts\python -m app.cli analyze --only faces
   # Choices: blur | captions | ocr | faces | geo
   ```

4. **From the UI**: Catalog page → "Analyze next 200 (N left)" button. Shows remaining count.

## Notes

- Stages run sequentially and release VRAM between them to avoid thermal throttling on the 1070.
- `captions` needs Ollama running (`OLLAMA_HOST`); `ocr`/`faces` use transformers/onnxruntime.
- `geo` uses the Places API if `GOOGLE_PLACES_API_KEY` is set; otherwise stores raw lat/long only.
- Re-running is safe: only un-analyzed (or `--force`) items are processed.
- Library size: ~27,859 photos → ~140 batches of 200. Run overnight with `--batch`.

## Rules

- Analysis is read-only on originals; results are written to the catalog only.
