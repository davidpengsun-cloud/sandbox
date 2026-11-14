-- Not a DDL onboarding takes: a CREATE TABLE AS SELECT would read a file on the server. Read DDL refuses it, naming the
-- statement, and writes nothing (README, Demo script step 10).
CREATE TABLE leasing.notes AS SELECT * FROM read_text('README.md');
