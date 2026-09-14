"""Catalog ORM models.

``media`` is the spine. Everything else references it. ``review_actions`` is the single gate for any
mutating action — nothing touches Google Photos until an action is explicitly approved and applied.
"""
from __future__ import annotations

from datetime import datetime
import builtins

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Media(Base):
    __tablename__ = "media"
    __table_args__ = (UniqueConstraint("sha256", name="uq_media_sha256"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    abs_path: Mapped[str] = mapped_column(String, index=True)
    rel_name: Mapped[str] = mapped_column(String)            # original filename
    media_type: Mapped[str] = mapped_column(String, index=True)  # "photo" | "video"
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    phash: Mapped[str | None] = mapped_column(String(32), index=True, default=None)

    width: Mapped[int | None] = mapped_column(Integer, default=None)
    height: Mapped[int | None] = mapped_column(Integer, default=None)
    bytes: Mapped[int | None] = mapped_column(Integer, default=None)
    taken_at: Mapped[datetime | None] = mapped_column(DateTime, index=True, default=None)

    source: Mapped[str] = mapped_column(String, default="takeout")
    thumbnail_sha256: Mapped[str | None] = mapped_column(String(64), default=None)
    preview_format: Mapped[str | None] = mapped_column(String(16), default=None)
    preview_skip_reason: Mapped[str | None] = mapped_column(String, default=None)
    clip_embedding: Mapped[builtins.bytes | None] = mapped_column(default=None)

    # geo (from Takeout sidecar / EXIF — never from the Photos API)
    gps_lat: Mapped[float | None] = mapped_column(Float, default=None)
    gps_lng: Mapped[float | None] = mapped_column(Float, default=None)
    place_id: Mapped[str | None] = mapped_column(String, index=True, default=None)
    place_name: Mapped[str | None] = mapped_column(String, default=None)

    # camera / codec
    camera: Mapped[str | None] = mapped_column(String, default=None)
    codec: Mapped[str | None] = mapped_column(String, default=None)
    bitrate: Mapped[int | None] = mapped_column(Integer, default=None)
    duration: Mapped[float | None] = mapped_column(Float, default=None)

    # analysis-derived
    is_portrait: Mapped[bool | None] = mapped_column(Boolean, default=None)
    # Sum of (face bbox area / image area) across ALL detected faces — unlike is_portrait (largest
    # face only), this also catches group shots where several medium faces jointly fill the frame.
    face_area_ratio: Mapped[float | None] = mapped_column(Float, default=None)
    blur_score: Mapped[float | None] = mapped_column(Float, default=None)
    thumb_path: Mapped[str | None] = mapped_column(String, default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)  # from Takeout sidecar
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    # Set when the file has been MOVED to the quarantine folder to reclaim disk space. The row stays
    # in the catalog and ``abs_path`` follows the file, so analysis and thumbnails keep working;
    # ``archived_from`` is the original location, which is what makes the move reversible.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, index=True, default=None)
    archived_from: Mapped[str | None] = mapped_column(String, default=None)

    captions: Mapped[list["Caption"]] = relationship(back_populates="media", cascade="all, delete-orphan")
    ocr: Mapped[list["OcrText"]] = relationship(back_populates="media", cascade="all, delete-orphan")
    faces: Mapped[list["Face"]] = relationship(back_populates="media", cascade="all, delete-orphan")
    derived: Mapped[list["DerivedMedia"]] = relationship(back_populates="source", cascade="all, delete-orphan")


class Caption(Base):
    __tablename__ = "captions"
    id: Mapped[int] = mapped_column(primary_key=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    tags: Mapped[str | None] = mapped_column(Text, default=None)  # comma-separated
    model: Mapped[str] = mapped_column(String)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    media: Mapped[Media] = relationship(back_populates="captions")


class OcrText(Base):
    __tablename__ = "ocr_text"
    id: Mapped[int] = mapped_column(primary_key=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String)
    media: Mapped[Media] = relationship(back_populates="ocr")


class FaceCluster(Base):
    __tablename__ = "face_clusters"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str | None] = mapped_column(String, default=None)  # user-assigned person name
    cover_media_id: Mapped[int | None] = mapped_column(Integer, default=None)
    faces: Mapped[list["Face"]] = relationship(back_populates="cluster")


class Face(Base):
    __tablename__ = "faces"
    id: Mapped[int] = mapped_column(primary_key=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), index=True)
    cluster_id: Mapped[int | None] = mapped_column(ForeignKey("face_clusters.id"), index=True, default=None)
    bbox: Mapped[str] = mapped_column(String)          # "x,y,w,h"
    embedding: Mapped[bytes | None] = mapped_column(default=None)  # float32 bytes
    det_score: Mapped[float | None] = mapped_column(Float, default=None)
    media: Mapped[Media] = relationship(back_populates="faces")
    cluster: Mapped[FaceCluster | None] = relationship(back_populates="faces")


class DupGroup(Base):
    __tablename__ = "dup_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    method: Mapped[str] = mapped_column(String)        # "sha256" | "phash" | "clip"
    keeper_media_id: Mapped[int | None] = mapped_column(Integer, default=None)
    # Short human phrase for why the keeper won ("sharpest · best centred"). Shown in the UI so a
    # user can accept the automatic choice at a glance instead of comparing every group by eye.
    keeper_reason: Mapped[str | None] = mapped_column(String, default=None)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    members: Mapped[list["DupMember"]] = relationship(back_populates="group", cascade="all, delete-orphan")


class DupMember(Base):
    __tablename__ = "dup_members"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("dup_groups.id", ondelete="CASCADE"), index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), index=True)
    score: Mapped[float | None] = mapped_column(Float, default=None)  # similarity / quality score
    group: Mapped[DupGroup] = relationship(back_populates="members")


class PlaceCluster(Base):
    __tablename__ = "place_clusters"
    id: Mapped[int] = mapped_column(primary_key=True)
    place_id: Mapped[str | None] = mapped_column(String, index=True, default=None)
    name: Mapped[str | None] = mapped_column(String, default=None)
    address: Mapped[str | None] = mapped_column(String, default=None)
    lat: Mapped[float | None] = mapped_column(Float, default=None)   # centroid
    lng: Mapped[float | None] = mapped_column(Float, default=None)   # centroid
    media_ids: Mapped[str | None] = mapped_column(Text, default=None)  # JSON list of member media ids
    # Set when the user has matched a place or split the cluster. Incremental auto-clustering folds new
    # photos in but never reassigns/destroys members, so manual edits persist across runs.
    manual: Mapped[bool] = mapped_column(Boolean, default=False)
    # User has already posted a Google Maps review for this place — hidden by default so you can focus
    # on the ones still to do.
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)


class IgnoredPlace(Base):
    """A place the user marked as personal/uninteresting (home, parents', …). Persistent: clustering
    skips photos that fall within ``radius_m`` of it, so an ignored place never re-clusters."""

    __tablename__ = "ignored_places"
    id: Mapped[int] = mapped_column(primary_key=True)
    place_id: Mapped[str | None] = mapped_column(String, index=True, default=None)
    lat: Mapped[float | None] = mapped_column(Float, default=None)
    lng: Mapped[float | None] = mapped_column(Float, default=None)
    key: Mapped[str] = mapped_column(String, index=True)             # rounded-coord bucket key
    label: Mapped[str] = mapped_column(String)                       # user label, e.g. "Home"
    radius_m: Mapped[float] = mapped_column(Float, default=120.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DerivedMedia(Base):
    __tablename__ = "derived_media"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), default=None)
    kind: Mapped[str] = mapped_column(String)          # "enhanced" | "compressed" | "highlight"
    path: Mapped[str] = mapped_column(String)
    meta: Mapped[str | None] = mapped_column(Text, default=None)  # JSON (e.g. size before/after)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending | approved | uploaded
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    source: Mapped[Media | None] = relationship(back_populates="derived")


class ReviewAction(Base):
    """The single gate for any mutating action against Google Photos / Maps."""

    __tablename__ = "review_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String, index=True)  # delete | upload | caption | review | highlight
    payload: Mapped[str] = mapped_column(Text)             # JSON describing the action
    status: Mapped[str] = mapped_column(String, default="pending", index=True)  # pending|approved|done|dismissed
    note: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class GpItem(Base):
    """One row per item that actually lives in Google Photos, as seen by the Chrome extension.

    The catalog (``media``) is built from a Takeout export and knows nothing about Google's own
    identifiers. This table is the bridge: the extension enumerates the live library over Google's
    internal ``batchexecute`` API and posts the results here, then ``pipeline/gp_match`` links each
    row to a ``media`` row so local analysis can drive real actions.

    ``media_id`` is nullable on purpose — items uploaded *after* the Takeout export have no local
    counterpart (no hash, no thumbnail, no CLIP embedding) and so cannot take part in local dedup.
    """

    __tablename__ = "gp_items"
    __table_args__ = (UniqueConstraint("media_key", name="uq_gp_media_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Per-item id. Used for album membership, getBatchMediaInfo and productUrl.
    media_key: Mapped[str] = mapped_column(String, index=True)
    # CONTENT identity. Every mutation (trash/restore/archive/description/timestamp) keys on this, NOT
    # media_key — the same bytes surfaced through a shared album get a different media_key but the
    # same dedup_key, and acting per-media_key fires redundant/conflicting ops.
    dedup_key: Mapped[str | None] = mapped_column(String, index=True, default=None)
    # "/u/0", "/u/1", … — guards against running an operation against the wrong signed-in account.
    account: Mapped[str] = mapped_column(String, index=True, default="/u/0")

    file_name: Mapped[str | None] = mapped_column(String, index=True, default=None)
    taken_at: Mapped[datetime | None] = mapped_column(DateTime, index=True, default=None)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    width: Mapped[int | None] = mapped_column(Integer, default=None)
    height: Mapped[int | None] = mapped_column(Integer, default=None)
    bytes: Mapped[int | None] = mapped_column(Integer, default=None)
    duration: Mapped[float | None] = mapped_column(Float, default=None)

    is_owned: Mapped[bool | None] = mapped_column(Boolean, default=None)
    # False => Google recompressed it ("storage saver"), so its byte count will NOT match Takeout.
    is_original_quality: Mapped[bool | None] = mapped_column(Boolean, default=None)
    trashed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    product_url: Mapped[str | None] = mapped_column(String, default=None)
    thumb_url: Mapped[str | None] = mapped_column(String, default=None)

    # The bridge back to the local catalog.
    media_id: Mapped[int | None] = mapped_column(
        ForeignKey("media.id", ondelete="SET NULL"), index=True, default=None)
    # name+ts | name+dims | name-unique | ts+dims | manual | ambiguous | none
    match_method: Mapped[str | None] = mapped_column(String, index=True, default=None)
    match_score: Mapped[float | None] = mapped_column(Float, default=None)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class GpOperation(Base):
    """A durable, resumable batch of mutations for the extension to execute against Google Photos.

    The server owns the cursor. The extension asks for the next slice, performs it, and reports the
    result — so killing the browser mid-run loses at most one batch instead of the whole operation.
    """

    __tablename__ = "gp_operations"

    id: Mapped[int] = mapped_column(primary_key=True)
    op: Mapped[str] = mapped_column(String, index=True)  # trash|restore|set_description|set_timestamp|add_to_album
    # Nullable only for operations the extension originates itself (e.g. an undo). Everything that
    # deletes must carry the approved ReviewAction that authorised it.
    review_action_id: Mapped[int | None] = mapped_column(
        ForeignKey("review_actions.id", ondelete="SET NULL"), index=True, default=None)
    account: Mapped[str] = mapped_column(String, default="/u/0")

    # JSON ordered list. For key-only ops: ["dedupKey", …]. For parameterised ops: [{"key":…, …}, …].
    payload: Mapped[str] = mapped_column(Text)
    # JSON of operation-level arguments that apply to every batch, e.g. {"title": "Goa"} for
    # add_to_album. Kept separate from payload so it isn't repeated on every one of 10,000 entries.
    args: Mapped[str | None] = mapped_column(Text, default=None)
    # JSON {"succeeded": [...], "failed": [{"key":…, "error":…}]} — succeeded[] is what undo replays.
    results: Mapped[str | None] = mapped_column(Text, default=None)

    total: Mapped[int] = mapped_column(Integer, default=0)
    done_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    cursor: Mapped[int] = mapped_column(Integer, default=0)  # index into payload — the resume point

    status: Mapped[str] = mapped_column(String, default="pending", index=True)  # pending|running|paused|done|failed|cancelled
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ScanCursor(Base):
    """Checkpoint persisted only after a page has been committed to the catalog."""
    __tablename__ = "scan_cursors"
    account: Mapped[str] = mapped_column(String, primary_key=True)
    page_id: Mapped[str | None] = mapped_column(Text, default=None)
    complete: Mapped[bool] = mapped_column(Boolean, default=False)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    items: Mapped[int] = mapped_column(Integer, default=0)
