"""init schema

Revision ID: 0001_init_schema
Revises: None
Create Date: 2026-02-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_init_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hotels",
        sa.Column("hotel_id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column("latitude", sa.Float(precision=53), nullable=False),
        sa.Column("longitude", sa.Float(precision=53), nullable=False),
        sa.Column("check_in_time", sa.Text(), nullable=True),
        sa.Column("check_out_time", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "hotel_amenities",
        sa.Column("hotel_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hotels.hotel_id"), nullable=False),
        sa.Column("amenity", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("hotel_id", "amenity"),
    )

    op.create_table(
        "hotel_content",
        sa.Column("hotel_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hotels.hotel_id"), primary_key=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("policies_summary", sa.Text(), nullable=True),
        sa.Column("images", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "offers",
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("hotel_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hotels.hotel_id"), nullable=False),
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

    op.create_table(
        "session_snapshots",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("user_id_hash", sa.Text(), nullable=False),
        sa.Column("agent_state", sa.Text(), nullable=False),
        sa.Column("constraints", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # Indexes (required)
    op.create_index("idx_hotels_tenant", "hotels", ["tenant_id"])
    op.create_index("idx_hotels_tenant_city", "hotels", ["tenant_id", "city"])
    op.create_index("idx_hotels_tenant_lat_lon", "hotels", ["tenant_id", "latitude", "longitude"])

    op.create_index("idx_offers_tenant_dates", "offers", ["tenant_id", "check_in", "check_out"])
    op.create_index("idx_offers_tenant_hotel", "offers", ["tenant_id", "hotel_id"])
    op.create_index("idx_offers_tenant_total", "offers", ["tenant_id", "total_price"])
    op.create_index("idx_offers_tenant_refundable", "offers", ["tenant_id", "refundable"])
    op.create_index("idx_offers_tenant_last_priced", "offers", ["tenant_id", "last_priced_ts"])


def downgrade() -> None:
    op.drop_index("idx_offers_tenant_last_priced", table_name="offers")
    op.drop_index("idx_offers_tenant_refundable", table_name="offers")
    op.drop_index("idx_offers_tenant_total", table_name="offers")
    op.drop_index("idx_offers_tenant_hotel", table_name="offers")
    op.drop_index("idx_offers_tenant_dates", table_name="offers")

    op.drop_index("idx_hotels_tenant_lat_lon", table_name="hotels")
    op.drop_index("idx_hotels_tenant_city", table_name="hotels")
    op.drop_index("idx_hotels_tenant", table_name="hotels")

    op.drop_table("session_snapshots")
    op.drop_table("offers")
    op.drop_table("hotel_content")
    op.drop_table("hotel_amenities")
    op.drop_table("hotels")

