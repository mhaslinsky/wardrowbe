import logging
from collections.abc import Iterator, Mapping
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Text, event, func, inspect, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from app.database import Base
from app.models.outfit import Outfit
from app.models.user import User


class PreviewPhoto(Base):
    __tablename__ = "preview_photos"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    paths: Mapped[dict] = mapped_column(JSONB)


class OutfitPreview(Base):
    __tablename__ = "outfit_previews"

    outfit_id: Mapped[UUID] = mapped_column(
        ForeignKey("outfits.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    cache_key: Mapped[str] = mapped_column(Text, default="")
    clothes_key: Mapped[str | None] = mapped_column(Text, deferred=True)
    preferences_key: Mapped[str | None] = mapped_column(Text, deferred=True)
    person_key: Mapped[str | None] = mapped_column(Text, deferred=True)
    paths: Mapped[dict] = mapped_column(JSONB, default=dict)
    claim_id: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)


class PreviewFeedback(Base):
    __tablename__ = "preview_feedback"
    __table_args__ = (
        Index(
            "uq_preview_comment",
            "user_id",
            "outfit_id",
            "preview_created_at",
            unique=True,
            postgresql_where=text("kind = 'comment'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    ai_call_id: Mapped[UUID | None] = mapped_column(index=True, nullable=True)
    comparison_call_id: Mapped[UUID | None] = mapped_column(nullable=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    outfit_id: Mapped[UUID | None] = mapped_column(ForeignKey("outfits.id", ondelete="SET NULL"))
    preview_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paths: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    kind: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


User.preview_photo = relationship(PreviewPhoto, cascade="all, delete-orphan", uselist=False)
User.outfit_previews = relationship(OutfitPreview, cascade="all, delete-orphan", overlaps="preview")
Outfit.preview = relationship(
    OutfitPreview, cascade="all, delete-orphan", uselist=False, overlaps="outfit_previews"
)


def feedback_table_exists(session):
    # Older migration revisions have no preview_feedback table.
    return inspect(session.connection()).has_table("preview_feedback")


@event.listens_for(Session, "before_flush")
def collect_deleted_preview_files(session, _context, _instances):
    paths = session.info.setdefault("preview_cleanup", {})
    deleted_users = [record for record in session.deleted if isinstance(record, User)]
    deleted_user_ids = {record.id for record in deleted_users}
    deleted_previews = {
        record for record in session.deleted if isinstance(record, (PreviewPhoto, OutfitPreview))
    }
    deleted_feedback = {record for record in session.deleted if isinstance(record, PreviewFeedback)}
    retained = set()
    owners = {record.user_id for record in deleted_feedback | deleted_previews} - deleted_user_ids
    if owners:
        retained = {
            path
            for feedback in session.scalars(
                select(PreviewFeedback).where(
                    PreviewFeedback.kind == "comment", PreviewFeedback.user_id.in_(owners)
                )
            )
            if feedback not in deleted_feedback
            for path in preview_file_paths(feedback.paths or {})
        }
    if deleted_feedback:
        retained.update(
            path
            for preview in session.scalars(
                select(OutfitPreview).where(OutfitPreview.user_id.in_(owners))
            )
            if preview not in deleted_previews
            for path in preview_file_paths(preview.paths)
        )
    if deleted_users and feedback_table_exists(session):
        for user in deleted_users:
            saved_paths = session.scalars(
                text(
                    "SELECT to_jsonb(feedback)->'paths' FROM preview_feedback AS feedback "
                    "WHERE user_id = :user_id"
                ),
                {"user_id": user.id},
            )
            for stored in saved_paths:
                if stored is not None:
                    paths.update({path: path for path in preview_file_paths(stored)})
    for record in session.deleted:
        if isinstance(record, Outfit):
            if record.selfie_paths:
                paths.update({path: path for path in preview_file_paths(record.selfie_paths)})
        if isinstance(record, (PreviewPhoto, OutfitPreview, PreviewFeedback)):
            paths.update(
                {
                    path: path
                    for path in preview_file_paths(record.paths or {})
                    if path not in retained
                }
            )


def preview_file_paths(paths: Mapping[str, str | dict[str, str]]) -> Iterator[str]:
    """Yield files from flat preview paths or references grouped by angle."""
    for value in paths.values():
        if isinstance(value, dict):
            yield from preview_file_paths(value)
        else:
            yield value


def delete_preview_files(paths, strict=False):
    from app.services.image_service import ImageService

    service = ImageService()
    for path in preview_file_paths(paths):
        try:
            service.delete_images({path: path})
        except OSError:
            logging.getLogger(__name__).warning("Preview file cleanup failed: %s", path)
            if strict:
                raise


@event.listens_for(Session, "after_commit")
def remove_preview_files(session):
    delete_preview_files(session.info.pop("preview_cleanup", {}))


@event.listens_for(Session, "after_rollback")
def discard_preview_cleanup(session):
    session.info.pop("preview_cleanup", None)
