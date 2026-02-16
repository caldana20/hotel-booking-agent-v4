from __future__ import annotations

import math
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from services.tools.app import observability
from services.tools.app.db import ENGINE, enforce_tenant, get_session
from services.tools.app.logging import configure_logging, logger
from services.tools.app.schemas import (
    GetOffersRequest,
    GetOffersResponse,
    HardFilters,
    RankOffersRequest,
    RankOffersResponse,
    SearchCandidatesRequest,
    SearchCandidatesResponse,
)
from services.tools.app.settings import SETTINGS


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _money(x: float) -> float:
    return math.floor(x * 100 + 0.5) / 100.0


def _normalize_city(v: str | None) -> str | None:
    """
    Tools should be resilient to common user/LLM formats like "Seattle, WA" or "Austin, TX".
    The seed data stores `hotels.city` as just the city name (e.g. "Seattle").
    """
    if not v:
        return v
    # Take the leading city token before any comma, trim, and case-fold later in SQL.
    city = v.split(",", 1)[0].strip()
    return city or v.strip()


def _apply_offer_hard_filters(where: list[Any], offers: Any, hard_filters: HardFilters | None) -> list[Any]:
    """
    Apply hard filters that live on the offers table (price/refundable).

    Centralizing this prevents filter drift between endpoints.
    """
    if not hard_filters:
        return where
    if hard_filters.max_price is not None:
        where.append(offers.c.total_price <= hard_filters.max_price)
    if hard_filters.refundable_only:
        where.append(offers.c.refundable.is_(True))
    return where


def _apply_offers_query_hard_filters(q: Any, offers: Any, hard_filters: HardFilters | None) -> Any:
    if not hard_filters:
        return q
    if hard_filters.max_price is not None:
        q = q.where(offers.c.total_price <= hard_filters.max_price)
    if hard_filters.refundable_only:
        q = q.where(offers.c.refundable.is_(True))
    return q


app = FastAPI(title="Hotel Tools API", version="0.1.0")
configure_logging(SETTINGS.log_level)
observability.setup_tracing(app, service_name="tools")
observability.add_metrics_middleware(app, service_name="tools")
observability.instrument_sqlalchemy(ENGINE)


@app.get("/healthz")
async def healthz(session: AsyncSession = Depends(get_session)) -> dict:
    await session.execute(sa.text("SELECT 1"))
    return {"ok": True}


@app.post("/tools/search_candidates", response_model=SearchCandidatesResponse)
async def search_candidates(
    req: SearchCandidatesRequest, session: AsyncSession = Depends(get_session)
) -> SearchCandidatesResponse:
    try:
        enforce_tenant(req.tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid tenant_id")

    hard_filters: HardFilters | None = req.hard_filters
    city = _normalize_city(req.location.city)
    geo = req.location.geo_box

    hotels = sa.table(
        "hotels",
        sa.column("hotel_id"),
        sa.column("tenant_id"),
        sa.column("name"),
        sa.column("latitude"),
        sa.column("longitude"),
        sa.column("star_rating"),
        sa.column("review_score"),
        sa.column("city"),
        sa.column("neighborhood"),
    )
    offers = sa.table(
        "offers",
        sa.column("hotel_id"),
        sa.column("tenant_id"),
        sa.column("check_in"),
        sa.column("check_out"),
        sa.column("adults"),
        sa.column("children"),
        sa.column("rooms"),
        sa.column("total_price"),
        sa.column("refundable"),
        sa.column("inventory_status"),
    )
    amenities = sa.table(
        "hotel_amenities",
        sa.column("hotel_id"),
        sa.column("amenity"),
    )

    # Base hotel filter.
    where = [hotels.c.tenant_id == req.tenant_id]
    if city:
        where.append(sa.func.lower(hotels.c.city) == city.lower())
    if geo:
        where.extend(
            [
                hotels.c.latitude >= geo.min_lat,
                hotels.c.latitude <= geo.max_lat,
                hotels.c.longitude >= geo.min_lon,
                hotels.c.longitude <= geo.max_lon,
            ]
        )
    if hard_filters and hard_filters.min_star is not None:
        where.append(hotels.c.star_rating >= hard_filters.min_star)

    # Candidate hotels must have at least one matching offer for the trip.
    offer_where = [
        offers.c.tenant_id == req.tenant_id,
        offers.c.check_in == req.check_in,
        offers.c.check_out == req.check_out,
        offers.c.adults == req.occupancy.adults,
        offers.c.children == req.occupancy.children,
        offers.c.rooms == req.occupancy.rooms,
        offers.c.inventory_status == "AVAILABLE",
    ]
    offer_where = _apply_offer_hard_filters(offer_where, offers, hard_filters)

    q = (
        sa.select(
            hotels.c.hotel_id,
            hotels.c.name,
            hotels.c.city,
            hotels.c.neighborhood,
            hotels.c.latitude,
            hotels.c.longitude,
            hotels.c.star_rating,
            hotels.c.review_score,
        )
        .select_from(hotels.join(offers, hotels.c.hotel_id == offers.c.hotel_id))
        .where(sa.and_(*where))
        .where(sa.and_(*offer_where))
        .group_by(
            hotels.c.hotel_id,
            hotels.c.name,
            hotels.c.city,
            hotels.c.neighborhood,
            hotels.c.latitude,
            hotels.c.longitude,
            hotels.c.star_rating,
            hotels.c.review_score,
        )
        .limit(SETTINGS.max_candidates)
    )

    # Amenities filter: require all requested amenities.
    if hard_filters and hard_filters.amenities:
        # Join and HAVING count distinct matched amenities == len(requested).
        requested = list(dict.fromkeys(hard_filters.amenities))
        q = (
            sa.select(
                hotels.c.hotel_id,
                hotels.c.name,
                hotels.c.city,
                hotels.c.neighborhood,
                hotels.c.latitude,
                hotels.c.longitude,
                hotels.c.star_rating,
                hotels.c.review_score,
            )
            .select_from(
                hotels.join(offers, hotels.c.hotel_id == offers.c.hotel_id).join(
                    amenities, amenities.c.hotel_id == hotels.c.hotel_id
                )
            )
            .where(sa.and_(*where))
            .where(sa.and_(*offer_where))
            .where(amenities.c.amenity.in_(requested))
            .group_by(
                hotels.c.hotel_id,
                hotels.c.name,
                hotels.c.city,
                hotels.c.neighborhood,
                hotels.c.latitude,
                hotels.c.longitude,
                hotels.c.star_rating,
                hotels.c.review_score,
            )
            .having(sa.func.count(sa.distinct(amenities.c.amenity)) == len(requested))
            .limit(SETTINGS.max_candidates)
        )

    rows = (await session.execute(q)).all()
    candidates = [
        dict(
            hotel_id=r.hotel_id,
            name=r.name,
            city=getattr(r, "city", None),
            neighborhood=getattr(r, "neighborhood", None),
            latitude=float(r.latitude),
            longitude=float(r.longitude),
            star_rating=float(r.star_rating) if r.star_rating is not None else None,
            review_score=float(r.review_score) if r.review_score is not None else None,
        )
        for r in rows
    ]

    # Counts (lightweight; bounded)
    offers_count_q = (
        sa.select(sa.func.count())
        .select_from(offers)
        .where(sa.and_(*offer_where))
    )
    offers_count = int((await session.execute(offers_count_q)).scalar_one())

    logger.info("tool_call_finished", tool_name="search_candidates", candidates=len(candidates), offers_matched=offers_count)

    return SearchCandidatesResponse(
        candidates=candidates,
        counts={"candidates": len(candidates), "offers_matched": offers_count},
    )


@app.post("/tools/get_offers", response_model=GetOffersResponse)
async def get_offers(req: GetOffersRequest, session: AsyncSession = Depends(get_session)) -> GetOffersResponse:
    try:
        enforce_tenant(req.tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid tenant_id")

    if len(req.hotel_ids) > SETTINGS.max_hotel_ids_per_request:
        raise HTTPException(status_code=400, detail="too many hotel_ids")

    offers = sa.table(
        "offers",
        sa.column("offer_id"),
        sa.column("tenant_id"),
        sa.column("hotel_id"),
        sa.column("check_in"),
        sa.column("check_out"),
        sa.column("adults"),
        sa.column("children"),
        sa.column("rooms"),
        sa.column("room_type"),
        sa.column("bed_config"),
        sa.column("rate_plan"),
        sa.column("currency"),
        sa.column("total_price"),
        sa.column("taxes_total"),
        sa.column("fees_total"),
        sa.column("refundable"),
        sa.column("cancellation_deadline"),
        sa.column("inventory_status"),
        sa.column("last_priced_ts"),
        sa.column("expires_ts"),
    )

    q = (
        sa.select(
            offers.c.offer_id,
            offers.c.hotel_id,
            offers.c.total_price,
            offers.c.taxes_total,
            offers.c.fees_total,
            offers.c.refundable,
            offers.c.cancellation_deadline,
            offers.c.inventory_status,
            offers.c.last_priced_ts,
            offers.c.expires_ts,
            offers.c.room_type,
            offers.c.bed_config,
            offers.c.rate_plan,
        )
        .where(
            sa.and_(
                offers.c.tenant_id == req.tenant_id,
                offers.c.hotel_id.in_(req.hotel_ids),
                offers.c.check_in == req.trip.check_in,
                offers.c.check_out == req.trip.check_out,
                offers.c.adults == req.trip.occupancy.adults,
                offers.c.children == req.trip.occupancy.children,
                offers.c.rooms == req.trip.occupancy.rooms,
                offers.c.currency == req.currency,
            )
        )
        .order_by(offers.c.total_price.asc())
        .limit(SETTINGS.max_offers)
    )

    q = _apply_offers_query_hard_filters(q, offers, req.hard_filters)

    rows = (await session.execute(q)).all()
    out = [
        dict(
            offer_id=r.offer_id,
            hotel_id=r.hotel_id,
            total_price=float(r.total_price),
            taxes_total=float(r.taxes_total),
            fees_total=float(r.fees_total),
            refundable=bool(r.refundable),
            cancellation_deadline=r.cancellation_deadline,
            inventory_status=r.inventory_status,
            last_priced_ts=r.last_priced_ts,
            expires_ts=r.expires_ts,
            room_type=r.room_type,
            bed_config=r.bed_config,
            rate_plan=r.rate_plan,
        )
        for r in rows
    ]

    logger.info("tool_call_finished", tool_name="get_offers", offers=len(out))
    return GetOffersResponse(offers=out)


@app.post("/tools/rank_offers", response_model=RankOffersResponse)
async def rank_offers(req: RankOffersRequest) -> RankOffersResponse:
    # Deterministic ranking based on offer fields only.
    weights = req.objective_weights or {}
    w_price = float(getattr(weights, "price", 0.6))
    w_ref = float(getattr(weights, "refundable", 0.3))
    w_fresh = float(getattr(weights, "freshness", 0.1))

    now = _now()
    offers_list = req.offers
    if not offers_list:
        return RankOffersResponse(ranked_offers=[], reasons=[])

    min_price = min(o.total_price for o in offers_list)
    max_price = max(o.total_price for o in offers_list)
    price_span = max(max_price - min_price, 1e-6)

    scored = []
    reasons = []
    for o in offers_list:
        # Normalize: lower price is better
        price_norm = 1.0 - ((o.total_price - min_price) / price_span)
        ref_norm = 1.0 if o.refundable else 0.0
        # Fresher pricing is better; cap at 6h
        age_sec = max((now - o.last_priced_ts).total_seconds(), 0.0)
        fresh_norm = 1.0 - min(age_sec / (6 * 3600), 1.0)

        score = w_price * price_norm + w_ref * ref_norm + w_fresh * fresh_norm
        scored.append((score, o))

        rs = [
            f"total_price={o.total_price}",
            f"refundable={o.refundable}",
            f"inventory_status={o.inventory_status}",
            f"last_priced_ts={o.last_priced_ts.isoformat()}",
            f"expires_ts={o.expires_ts.isoformat()}",
        ]
        if o.cancellation_deadline:
            rs.append(f"cancellation_deadline={o.cancellation_deadline.isoformat()}")
        reasons.append({"offer_id": o.offer_id, "reasons": rs})

    scored.sort(key=lambda t: t[0], reverse=True)
    ranked = [{"offer": o.model_dump(), "score": float(s)} for s, o in scored]

    logger.info("tool_call_finished", tool_name="rank_offers", ranked=len(ranked))
    return RankOffersResponse(ranked_offers=ranked, reasons=reasons)

