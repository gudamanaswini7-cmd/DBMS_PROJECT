-- SQLite-compatible schema for the e-waste management faculty requirements.
-- SQLite uses CHECK constraints to provide the requested ENUM values.

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('staff', 'citizen'))
);

CREATE TABLE IF NOT EXISTS ewaste_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    weight REAL NOT NULL CHECK (weight > 0),
    description TEXT NOT NULL DEFAULT '',
    image_path TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'completed')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    pickup_date TEXT NOT NULL,
    pickup_address TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'completed')),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY (item_id) REFERENCES ewaste_items (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL UNIQUE,
    weight REAL NOT NULL CHECK (weight > 0),
    reward_amount REAL NOT NULL CHECK (reward_amount >= 0),
    date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY (item_id) REFERENCES ewaste_items (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ewaste_items_user
    ON ewaste_items (user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_collections_status_date
    ON collections (status, pickup_date);
CREATE INDEX IF NOT EXISTS idx_transactions_user
    ON transactions (user_id, date);
