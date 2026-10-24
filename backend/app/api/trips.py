from datetime import date
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.trip import Trip
from app.models.user import User
from app.services.recommendation_service import AIRecommendationError, InsufficientWardrobeError
from app.services.trip_service import TripService
from app.services.weather_service import WeatherServiceError
from app.utils.auth import get_current_user
from app.utils.garment_vocabulary import OCCASIONS

router = APIRouter(prefix="/trips", tags=["Trips"])


class TripRequest(BaseModel):
    destination: str = Field(min_length=1, max_length=200)
    start_date: date
    end_date: date
    occasions: list[str] = Field(min_length=1, max_length=14)

    @model_validator(mode="after")
    def validate_trip(self):
        self.destination = self.destination.strip()
        if not self.destination or not 0 <= (self.end_date - self.start_date).days < 14:
            raise ValueError("Choose a destination and 1 to 14 days")
        if any(occasion not in OCCASIONS for occasion in self.occasions):
            raise ValueError("Unknown occasion")
        return self


class TripResponse(TripRequest):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    days: list[dict]
    packing: list[dict]


@router.get("", response_model=list[TripResponse])
async def list_trips(
    user: Annotated[User, Depends(get_current_user)], db: Annotated[AsyncSession, Depends(get_db)]
):
    return (
        (
            await db.execute(
                select(Trip).where(Trip.user_id == user.id).order_by(Trip.start_date.desc())
            )
        )
        .scalars()
        .all()
    )


@router.post("", response_model=TripResponse, status_code=201)
async def plan_trip(
    request: TripRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        return await TripService(db).plan(user, request)
    except (ValueError, InsufficientWardrobeError) as error:
        raise HTTPException(422, detail=str(error)) from error
    except (httpx.HTTPError, WeatherServiceError, AIRecommendationError) as error:
        raise HTTPException(503, detail="Trip weather unavailable") from error
