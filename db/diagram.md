# Database diagram (ERD)

```mermaid
erDiagram
    HOTELS {
        UUID hotel_id PK
        TEXT tenant_id
        TEXT name
        TEXT brand
        NUMERIC star_rating
        NUMERIC review_score
        INT review_count
        TEXT address_line1
        TEXT address_line2
        TEXT city
        TEXT state
        TEXT postal_code
        TEXT country
        TEXT neighborhood
        FLOAT latitude
        FLOAT longitude
        TEXT check_in_time
        TEXT check_out_time
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    HOTEL_AMENITIES {
        UUID hotel_id PK, FK
        TEXT amenity PK
    }

    HOTEL_CONTENT {
        UUID hotel_id PK, FK
        TEXT description
        TEXT policies_summary
        JSONB images
        TIMESTAMPTZ updated_at
    }

    OFFERS {
        UUID offer_id PK
        TEXT tenant_id
        UUID hotel_id FK
        DATE check_in
        DATE check_out
        INT adults
        INT children
        INT rooms
        TEXT room_type
        TEXT bed_config
        TEXT rate_plan
        TEXT currency
        NUMERIC base_total
        NUMERIC taxes_total
        NUMERIC fees_total
        NUMERIC total_price
        BOOLEAN refundable
        TIMESTAMPTZ cancellation_deadline
        TEXT inventory_status
        TIMESTAMPTZ last_priced_ts
        TIMESTAMPTZ expires_ts
        TIMESTAMPTZ created_at
    }

    SESSION_SNAPSHOTS {
        UUID session_id PK
        TEXT tenant_id
        TEXT user_id_hash
        TEXT agent_state
        JSONB constraints
        JSONB snapshot
        TIMESTAMPTZ updated_at
    }

    HOTELS ||--o{ OFFERS : "hotel_id"
    HOTELS ||--o{ HOTEL_AMENITIES : "hotel_id"
    HOTELS ||--o| HOTEL_CONTENT : "hotel_id"
```

Notes:
- Source of truth: `db/migrations/alembic/versions/0001_init_schema.py`
- Multi-tenancy is modeled as a `tenant_id` column (enforced in app code as a single-tenant MVP).
- `session_snapshots` is not FK-linked to other tables (it stores agent session state as JSON).

