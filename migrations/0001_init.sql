PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS stock_prices (
  code TEXT NOT NULL,
  date TEXT NOT NULL,
  name TEXT,
  close REAL,
  high REAL,
  low REAL,
  open REAL,
  volume INTEGER,
  PRIMARY KEY (code, date)
);

CREATE INDEX IF NOT EXISTS idx_stock_prices_date
ON stock_prices (date DESC);

CREATE TABLE IF NOT EXISTS fundamentals (
  code TEXT PRIMARY KEY,
  sector TEXT,
  pe REAL,
  pb REAL,
  div_yield REAL,
  eps REAL,
  eps_year TEXT,
  eps_quarter TEXT,
  earnings_growth REAL,
  foreign_net INTEGER,
  trust_net INTEGER,
  dealer_net INTEGER,
  revenue_current INTEGER,
  revenue_yoy REAL,
  margin_today INTEGER,
  margin_change INTEGER,
  updated_date TEXT
);

CREATE TABLE IF NOT EXISTS market_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
