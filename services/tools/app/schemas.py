from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Occupancy(StrictModel):
    adults: Annotated[int, Field(ge=1, le=8)]
    children: Annotated[int, Field(ge=0, le=6)] = 0
    rooms: Annotated[int, Field(ge=1, le=4)] = 1


class LocationCity(StrictModel):
    city: str


class GeoBox(StrictModel):
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float


class Location(StrictModel):
    city: str | None = None
    geo_box: GeoBox | None = None

    @model_validator(mode="after")
    def _xor(self):
        if (self.city is None) == (self.geo_box is None):
            raise ValueError("location must specify exactly one of city or geo_box")
        return self


class HardFilters(StrictModel):
    max_price: float | None = Field(default=None, gt=0)
    min_star: float | None = Field(default=None, ge=0, le=5)
    amenities: list[str] | None = None
    # If true, only return refundable offers/hotels with refundable inventory for the trip.
    refundable_only: bool | None = None


class SearchCandidatesRequest(StrictModel):
    tenant_id: str
    location: Location
    check_in: date
    check_out: date
    occupancy: Occupancy
    hard_filters: HardFilters | None = None

    @model_validator(mode="after")
    def _dates(self):
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        return self


class CandidateHotel(StrictModel):
    hotel_id: UUID
    name: str
    city: str | None = None
    neighborhood: str | None = None
    latitude: float
    longitude: float
    star_rating: float | None = None
    review_score: float | None = None


class SearchCandidatesResponse(StrictModel):
    candidates: list[CandidateHotel]
    counts: dict[str, int]


class Trip(StrictModel):
    check_in: date
    check_out: date
    occupancy: Occupancy

    @model_validator(mode="after")
    def _dates(self):
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        return self


class GetOffersRequest(StrictModel):
    tenant_id: str
    hotel_ids: list[UUID]
    trip: Trip
    currency: str = Field(min_length=3, max_length=3)
    hard_filters: HardFilters | None = None

    @field_validator("hotel_ids")
    @classmethod
    def _non_empty(cls, v):
        if not v:
            raise ValueError("hotel_ids must be non-empty")
        return v


class Offer(StrictModel):
    offer_id: UUID
    hotel_id: UUID
    total_price: float
    taxes_total: float
    fees_total: float
    refundable: bool
    cancellation_deadline: datetime | None
    inventory_status: str
    last_priced_ts: datetime
    expires_ts: datetime
    room_type: str
    bed_config: str | None
    rate_plan: str


class GetOffersResponse(StrictModel):
    offers: list[Offer]


class UserPrefs(StrictModel):
    refundable_preferred: bool | None = None
    max_price: float | None = Field(default=None, gt=0)


class ObjectiveWeights(StrictModel):
    price: float = 0.6
    refundable: float = 0.3
    freshness: float = 0.1


class RankOffersRequest(StrictModel):
    offers: list[Offer]
    user_prefs: UserPrefs | None = None
    objective_weights: ObjectiveWeights | None = None


class RankedOffer(StrictModel):
    offer: Offer
    score: float


class RankReason(StrictModel):
    offer_id: UUID
    reasons: list[str]


class RankOffersResponse(StrictModel):
    ranked_offers: list[RankedOffer]
    reasons: list[RankReason]

