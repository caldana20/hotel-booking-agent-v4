from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from db.settings import SETTINGS


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _det_uuid(*parts: str) -> uuid.UUID:
    h = hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()
    return uuid.UUID(h[:32])


@dataclass(frozen=True)
class CitySpec:
    city: str
    state: str
    country: str
    # Rough centroid for clustering.
    lat0: float
    lon0: float
    neighborhoods: list[str]


CITY_SPECS: list[CitySpec] = [
    CitySpec(
        city="Austin",
        state="TX",
        country="US",
        lat0=30.2672,
        lon0=-97.7431,
        neighborhoods=["Downtown", "South Congress", "East Austin", "Zilker", "The Domain"],
    ),
    CitySpec(
        city="San Diego",
        state="CA",
        country="US",
        lat0=32.7157,
        lon0=-117.1611,
        neighborhoods=["Gaslamp", "La Jolla", "Mission Beach", "Little Italy", "North Park"],
    ),
    CitySpec(
        city="Chicago",
        state="IL",
        country="US",
        lat0=41.8781,
        lon0=-87.6298,
        neighborhoods=["Loop", "River North", "West Loop", "Wicker Park", "Hyde Park"],
    ),
    CitySpec(
        city="Seattle",
        state="WA",
        country="US",
        lat0=47.6062,
        lon0=-122.3321,
        neighborhoods=["Downtown", "Capitol Hill", "Belltown", "South Lake Union", "Fremont"],
    ),
]


AMENITIES = [
    "wifi",
    "breakfast_included",
    "pool",
    "gym",
    "parking",
    "pet_friendly",
    "airport_shuttle",
    "spa",
    "restaurant",
    "bar",
]


ROOM_TYPES = ["Standard King", "Standard Queen", "Deluxe King", "Studio Suite", "One Bedroom Suite"]
BED_CONFIGS = ["1 King", "2 Queens", "1 Queen", "2 Doubles"]
RATE_PLANS = ["Member Rate", "Flexible", "Advance Purchase", "Breakfast Package"]


def _jitter(rng: random.Random, scale: float) -> float:
    # Approx gaussian-like by summing uniforms.
    return (rng.random() + rng.random() + rng.random() - 1.5) * scale


def _round_money(x: float) -> float:
    return math.floor(x * 100 + 0.5) / 100.0


def seed(
    database_url: str,
    tenant_id: str,
    seed_value: int,
    hotels_n: int,
    offers_n: int,
    *,
    full_year_2026: bool = False,
    baseline_year: int = 2026,
    stay_len_min: int = 1,
    stay_len_max: int = 7,
    baseline_adults: list[int] | None = None,
    insert_batch_size: int = 10_000,
) -> None:
    rng = random.Random(seed_value)
    now = _now()

    engine = sa.create_engine(database_url, future=True)
    meta = sa.MetaData()

    hotels = sa.Table(
        "hotels",
        meta,
        sa.Column("hotel_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("brand", sa.Text(), nullable=True),
        sa.Column("star_rating", sa.Numeric(), nullable=True),
        sa.Column("review_score", sa.Numeric(), nullable=True),
        sa.Column("review_count", sa.Integer(), nullable=True),
        sa.Column("address_line1", sa.Text(), nullable=True),
        sa.Column("address_line2", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("postal_code", sa.Text(), nullable=True),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("neighborhood", sa.Text(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("check_in_time", sa.Text(), nullable=True),
        sa.Column("check_out_time", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    hotel_amenities = sa.Table(
        "hotel_amenities",
        meta,
        sa.Column("hotel_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("amenity", sa.Text(), primary_key=True),
    )
    hotel_content = sa.Table(
        "hotel_content",
        meta,
        sa.Column("hotel_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("policies_summary", sa.Text(), nullable=True),
        sa.Column("images", JSONB, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    offers = sa.Table(
        "offers",
        meta,
        sa.Column("offer_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("hotel_id", UUID(as_uuid=True), nullable=False),
        sa.Column("check_in", sa.Date(), nullable=False),
        sa.Column("check_out", sa.Date(), nullable=False),
        sa.Column("adults", sa.Integer(), nullable=False),
        sa.Column("children", sa.Integer(), nullable=False),
        sa.Column("rooms", sa.Integer(), nullable=False),
        sa.Column("room_type", sa.Text(), nullable=False),
        sa.Column("bed_config", sa.Text(), nullable=True),
        sa.Column("rate_plan", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("base_total", sa.Numeric(), nullable=False),
        sa.Column("taxes_total", sa.Numeric(), nullable=False),
        sa.Column("fees_total", sa.Numeric(), nullable=False),
        sa.Column("total_price", sa.Numeric(), nullable=False),
        sa.Column("refundable", sa.Boolean(), nullable=False),
        sa.Column("cancellation_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("inventory_status", sa.Text(), nullable=False),
        sa.Column("last_priced_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # Generate hotels across cities.
    hotel_rows: list[dict] = []
    amenity_rows: list[dict] = []
    content_rows: list[dict] = []

    brands = ["Apex", "Harbor", "Civic", "Summit", "Oak & Ivy", "MetroStay", "Sunset"]

    for i in range(hotels_n):
        city_spec = CITY_SPECS[i % len(CITY_SPECS)]
        neighborhood = rng.choice(city_spec.neighborhoods)
        lat = city_spec.lat0 + _jitter(rng, 0.04)
        lon = city_spec.lon0 + _jitter(rng, 0.05)

        hotel_id = _det_uuid(tenant_id, "hotel", str(i))
        brand = rng.choice(brands)
        star = rng.choice([3.0, 3.5, 4.0, 4.5, 5.0])
        review_score = _round_money(rng.uniform(3.6, 4.8))
        review_count = int(rng.triangular(20, 2500, 220))

        hotel_name = f"{brand} {neighborhood} Hotel"
        address_line1 = f"{100 + (i % 800)} {rng.choice(['Main', 'Market', 'Congress', 'Pine', 'Lake'])} St"
        postal_code = f"{rng.randint(10000, 99999)}"

        hotel_rows.append(
            dict(
                hotel_id=hotel_id,
                tenant_id=tenant_id,
                name=hotel_name,
                brand=brand,
                star_rating=star,
                review_score=review_score,
                review_count=review_count,
                address_line1=address_line1,
                address_line2=None,
                city=city_spec.city,
                state=city_spec.state,
                postal_code=postal_code,
                country=city_spec.country,
                neighborhood=neighborhood,
                latitude=lat,
                longitude=lon,
                check_in_time="15:00",
                check_out_time="11:00",
                created_at=now,
                updated_at=now,
            )
        )

        # Amenities: 4-8
        for amenity in rng.sample(AMENITIES, k=rng.randint(4, 8)):
            amenity_rows.append({"hotel_id": hotel_id, "amenity": amenity})

        images = [{"url": f"https://example.invalid/images/{hotel_id}/{j}.jpg", "alt": "Hotel"} for j in range(1, 6)]
        content_rows.append(
            dict(
                hotel_id=hotel_id,
                description=f"Modern hotel in {neighborhood}, {city_spec.city}.",
                policies_summary="No smoking. Government-issued ID required at check-in.",
                images=images,
                updated_at=now,
            )
        )

    # Generate offers.
    #
    # Important for the MVP UX: search requires an exact (check_in, check_out, occupancy) match.
    # So we generate a baseline grid per hotel, then optionally add additional random variety.
    today = date.today()
    max_days = 180  # quick-seed horizon

    if baseline_adults is None:
        baseline_adults = [2]

    def add_offer(
        *,
        offer_key: str,
        hotel_id: uuid.UUID,
        check_in: date,
        check_out: date,
        adults: int,
        children: int,
        rooms: int,
        room_type: str,
        bed_config: str | None,
        rate_plan: str,
        base_total: float,
        taxes_total: float,
        fees_total: float,
        refundable: bool,
        inventory_status: str,
        out_rows: list[dict],
    ) -> None:
        total = base_total + taxes_total + fees_total
        cancellation_deadline = None
        if refundable:
            cancellation_deadline = datetime.combine(check_in, datetime.min.time(), tzinfo=UTC) - timedelta(hours=48)

        # Ensure deterministic uniqueness across baseline + variety generations.
        offer_id = _det_uuid(
            tenant_id,
            "offer",
            offer_key,
            str(hotel_id),
            check_in.isoformat(),
            check_out.isoformat(),
            str(adults),
            str(children),
            str(rooms),
            room_type,
            str(bed_config),
            rate_plan,
        )
        last_priced_ts = now - timedelta(minutes=rng.randint(0, 180))
        expires_ts = last_priced_ts + timedelta(minutes=30)

        out_rows.append(
            dict(
                offer_id=offer_id,
                tenant_id=tenant_id,
                hotel_id=hotel_id,
                check_in=check_in,
                check_out=check_out,
                adults=adults,
                children=children,
                rooms=rooms,
                room_type=room_type,
                bed_config=bed_config,
                rate_plan=rate_plan,
                currency="USD",
                base_total=_round_money(base_total),
                taxes_total=_round_money(taxes_total),
                fees_total=_round_money(fees_total),
                total_price=_round_money(total),
                refundable=refundable,
                cancellation_deadline=cancellation_deadline,
                inventory_status=inventory_status,
                last_priced_ts=last_priced_ts,
                expires_ts=expires_ts,
                created_at=now,
            )
        )

    # Load into DB (truncate existing rows for deterministic idempotence in dev).
    with engine.begin() as conn:
        conn.execute(sa.text("TRUNCATE TABLE hotel_amenities, hotel_content, offers, session_snapshots, hotels CASCADE"))
        conn.execute(hotels.insert(), hotel_rows)
        conn.execute(hotel_amenities.insert(), amenity_rows)
        conn.execute(hotel_content.insert(), content_rows)

        offers_count = 0
        batch: list[dict] = []

        def flush() -> None:
            nonlocal offers_count, batch
            if not batch:
                return
            conn.execute(offers.insert(), batch)
            offers_count += len(batch)
            batch = []

        if full_year_2026:
            start = date(baseline_year, 1, 1)
            end = date(baseline_year, 12, 31)
            stay_lengths = list(range(stay_len_min, stay_len_max + 1))
            # Dense grid: all hotels x all 2026 check_in dates x stay lengths x baseline_adults.
            for h_i, h in enumerate(hotel_rows):
                hotel_id = h["hotel_id"]
                star = float(h["star_rating"] or 4.0)
                # Star rating influences baseline nightly. Keep stable per hotel.
                nightly = rng.triangular(110.0, 420.0, 160.0) * (0.85 + 0.08 * star)
                check_in = start
                while check_in <= end:
                    for stay_len in stay_lengths:
                        check_out = check_in + timedelta(days=stay_len)
                        for adults in baseline_adults:
                            room_type = rng.choice(ROOM_TYPES)
                            bed_config = rng.choice(BED_CONFIGS)
                            rate_plan = rng.choice(RATE_PLANS)
                            base = nightly * stay_len
                            taxes = base * rng.uniform(0.09, 0.15)
                            fees = rng.uniform(8.0, 22.0) * stay_len
                            refundable = rng.random() < 0.6
                            inventory_status = "SOLD_OUT" if rng.random() < 0.05 else "AVAILABLE"
                            add_offer(
                                offer_key=f"y{baseline_year}_h{h_i}_{check_in.isoformat()}_{stay_len}_a{adults}",
                                hotel_id=hotel_id,
                                check_in=check_in,
                                check_out=check_out,
                                adults=adults,
                                children=0,
                                rooms=1,
                                room_type=room_type,
                                bed_config=bed_config,
                                rate_plan=rate_plan,
                                base_total=base,
                                taxes_total=taxes,
                                fees_total=fees,
                                refundable=refundable,
                                inventory_status=inventory_status,
                                out_rows=batch,
                            )
                            if len(batch) >= insert_batch_size:
                                flush()
                    check_in = check_in + timedelta(days=1)

            flush()
        else:
            # Quick-seed baseline: daily per hotel, fixed 2-night stay, baseline_adults.
            for h_i, h in enumerate(hotel_rows):
                hotel_id = h["hotel_id"]
                star = float(h["star_rating"] or 4.0)
                nightly = rng.triangular(110.0, 420.0, 160.0) * (0.85 + 0.08 * star)
                for offset in range(0, max_days - 2, 1):
                    check_in = today + timedelta(days=offset)
                    check_out = check_in + timedelta(days=2)
                    stay_len = 2
                    for adults in baseline_adults:
                        room_type = rng.choice(ROOM_TYPES)
                        bed_config = rng.choice(BED_CONFIGS)
                        rate_plan = rng.choice(RATE_PLANS)
                        base = nightly * stay_len
                        taxes = base * rng.uniform(0.09, 0.15)
                        fees = rng.uniform(8.0, 22.0) * stay_len
                        refundable = rng.random() < 0.6
                        inventory_status = "SOLD_OUT" if rng.random() < 0.05 else "AVAILABLE"
                        add_offer(
                            offer_key=f"baseline_{offset}_a{adults}_h{h_i}",
                            hotel_id=hotel_id,
                            check_in=check_in,
                            check_out=check_out,
                            adults=adults,
                            children=0,
                            rooms=1,
                            room_type=room_type,
                            bed_config=bed_config,
                            rate_plan=rate_plan,
                            base_total=base,
                            taxes_total=taxes,
                            fees_total=fees,
                            refundable=refundable,
                            inventory_status=inventory_status,
                            out_rows=batch,
                        )
                        if len(batch) >= insert_batch_size:
                            flush()

            # Additional variety: random occupancy/length until we reach offers_n.
            # (This improves coverage for non-baseline queries.)
            target = max(offers_n, offers_count + len(batch))
            var_i = 0
            while offers_count + len(batch) < target:
                hotel_idx = rng.randrange(hotels_n)
                hotel_id = hotel_rows[hotel_idx]["hotel_id"]

                stay_len = rng.choice([1, 2, 2, 3, 3, 4])
                start_offset = rng.randrange(0, max_days - stay_len)
                check_in = today + timedelta(days=start_offset)
                check_out = check_in + timedelta(days=stay_len)

                adults = rng.choice([1, 2, 2, 2, 3, 4])
                children = rng.choice([0, 0, 0, 1, 2])
                rooms = 1

                room_type = rng.choice(ROOM_TYPES)
                bed_config = rng.choice(BED_CONFIGS)
                rate_plan = rng.choice(RATE_PLANS)

                base = rng.triangular(110.0, 680.0, 210.0) * stay_len
                taxes = base * rng.uniform(0.08, 0.16)
                fees = rng.uniform(5.0, 35.0) * stay_len

                refundable = rng.random() < 0.55
                inventory_status = "SOLD_OUT" if rng.random() < 0.08 else "AVAILABLE"
                add_offer(
                    offer_key=f"var_{var_i}",
                    hotel_id=hotel_id,
                    check_in=check_in,
                    check_out=check_out,
                    adults=adults,
                    children=children,
                    rooms=rooms,
                    room_type=room_type,
                    bed_config=bed_config,
                    rate_plan=rate_plan,
                    base_total=base,
                    taxes_total=taxes,
                    fees_total=fees,
                    refundable=refundable,
                    inventory_status=inventory_status,
                    out_rows=batch,
                )
                var_i += 1
                if len(batch) >= insert_batch_size:
                    flush()

            flush()

        counts = {}
        for table in ["hotels", "hotel_amenities", "hotel_content", "offers"]:
            counts[table] = conn.execute(sa.text(f"SELECT COUNT(1) FROM {table}")).scalar_one()

    # Verify minimums
    assert counts["hotels"] >= 200, counts
    assert counts["offers"] >= 2000, counts

    print(json.dumps({"seed": seed_value, "tenant_id": tenant_id, "counts": counts}, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or SETTINGS.database_url)
    parser.add_argument("--tenant-id", default=os.getenv("DEFAULT_TENANT_ID") or SETTINGS.default_tenant_id)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--hotels", type=int, default=220)
    parser.add_argument("--offers", type=int, default=2600)
    parser.add_argument("--full-year-2026", action="store_true", help="Generate offers for all 2026 check-in dates.")
    parser.add_argument("--baseline-adults", default=None, help="Comma-separated adults counts for baseline grid (e.g. 1,2).")
    parser.add_argument("--stay-len-min", type=int, default=1)
    parser.add_argument("--stay-len-max", type=int, default=7)
    parser.add_argument("--insert-batch-size", type=int, default=10_000)
    args = parser.parse_args()
    baseline_adults = None
    if args.baseline_adults:
        baseline_adults = [int(x.strip()) for x in str(args.baseline_adults).split(",") if x.strip()]
    seed(
        args.database_url,
        args.tenant_id,
        args.seed,
        args.hotels,
        args.offers,
        full_year_2026=bool(args.full_year_2026),
        baseline_year=2026,
        stay_len_min=args.stay_len_min,
        stay_len_max=args.stay_len_max,
        baseline_adults=baseline_adults,
        insert_batch_size=args.insert_batch_size,
    )


if __name__ == "__main__":
    main()

