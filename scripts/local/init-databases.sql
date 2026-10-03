-- Runs once for a fresh local PostgreSQL volume. CRUD uses POSTGRES_DB=chat_app.
-- Existing volumes are preserved; this does not move any tables or Run data.
CREATE DATABASE agent;
