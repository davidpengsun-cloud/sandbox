CREATE TABLE ledger (
  entry_id INTEGER,
  amount INTEGER,
  branch_code VARCHAR,
  valid_from DATE,
  valid_to DATE,
  is_current BOOLEAN,
  PRIMARY KEY (entry_id)
);

INSERT INTO ledger (entry_id, amount, branch_code, valid_from, valid_to, is_current) VALUES (1, 100, NULL, DATE '2025-01-01', DATE '9999-12-31', TRUE);
INSERT INTO ledger (entry_id, amount, branch_code, valid_from, valid_to, is_current) VALUES (2, 250, NULL, DATE '2025-01-01', DATE '9999-12-31', TRUE);
