-- Storage for immich-omni. Deliberately kept out of the schema Immich owns.
-- Run by `immich-omni init-db` as the `omni` role.

CREATE TABLE IF NOT EXISTS omni.assets (
    asset_id            uuid PRIMARY KEY,
    original_file_name  text,
    file_created_at     timestamptz,
    local_date_time     timestamptz,
    width               integer,
    height              integer,
    status              text NOT NULL DEFAULT 'pending',
    description         text,
    description_model   text,
    description_at      timestamptz,
    embedding           public.vector(1024),
    embedding_model     text,
    embedding_at        timestamptz,
    attempts            integer NOT NULL DEFAULT 0,
    last_error          text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT assets_status_check CHECK (
        status IN ('pending', 'described', 'embedded', 'failed')
    )
);

CREATE INDEX IF NOT EXISTS assets_status_idx ON omni.assets (status);

CREATE INDEX IF NOT EXISTS assets_embedding_hnsw
    ON omni.assets USING hnsw (embedding public.vector_cosine_ops)
    WHERE embedding IS NOT NULL;
