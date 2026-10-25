import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Text, event, func
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
    paths: Mapped[dict] = mapped_column(JSONB, default=dict)
    claim_id: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)


User.preview_photo = relationship(PreviewPhoto, cascade="all, delete-orphan", uselist=False)
User.outfit_previews = relationship(OutfitPreview, cascade="all, delete-orphan", overlaps="preview")
Outfit.preview = relationship(
    OutfitPreview, cascade="all, delete-orphan", uselist=False, overlaps="outfit_previews"
)


@event.listens_for(Session, "before_flush")
def collect_deleted_preview_files(session, _context, _instances):
    paths = session.info.setdefault("preview_cleanup", {})
    for record in session.deleted:
        if isinstance(record, (PreviewPhoto, OutfitPreview)):
            paths.update({path: path for path in preview_file_paths(record.paths)})


def preview_file_paths(paths):
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
