-- Public Cloudflare Access signing keys cached from the Access issuer by the
-- egress-enabled worker. The API uses this cache and stays on internal-only
-- Docker networks.
CREATE TABLE access_jwks_cache (
  id SMALLINT PRIMARY KEY CHECK (id = 1),
  jwks JSONB NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL,
  CHECK (jsonb_typeof(jwks) = 'object')
);

REVOKE ALL ON access_jwks_cache FROM PUBLIC;
