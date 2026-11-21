CREATE SCHEMA taproom;
CREATE TABLE taproom.member (
  member_id INTEGER PRIMARY KEY,
  joined_on DATE NOT NULL,
  member_band VARCHAR NOT NULL
);
COMMENT ON TABLE taproom.member IS 'Invented taproom membership records.';
COMMENT ON COLUMN taproom.member.member_id IS 'Sequential invented member key.';
COMMENT ON COLUMN taproom.member.joined_on IS 'Date the invented membership began.';
COMMENT ON COLUMN taproom.member.member_band IS 'Invented membership band.';
CREATE TABLE taproom.pour (
  pour_id INTEGER PRIMARY KEY,
  member_id INTEGER REFERENCES taproom.member(member_id),
  poured_on DATE NOT NULL,
  volume_litres DECIMAL(10,2) NOT NULL,
  takings_gbp DECIMAL(12,2) NOT NULL
);
COMMENT ON TABLE taproom.pour IS 'Invented taproom pours and takings.';
COMMENT ON COLUMN taproom.pour.pour_id IS 'Sequential invented pour key.';
COMMENT ON COLUMN taproom.pour.member_id IS 'Member linked to the invented pour.';
COMMENT ON COLUMN taproom.pour.poured_on IS 'Date of the invented pour.';
COMMENT ON COLUMN taproom.pour.volume_litres IS 'Invented volume poured in litres.';
COMMENT ON COLUMN taproom.pour.takings_gbp IS 'Invented taproom takings in pounds sterling.';

CREATE SCHEMA cellar;
CREATE TABLE cellar.brew (
  brew_id INTEGER PRIMARY KEY,
  brewed_on DATE NOT NULL,
  style_code VARCHAR NOT NULL,
  ingredient_cost_gbp DECIMAL(12,2) NOT NULL
);
COMMENT ON TABLE cellar.brew IS 'Invented brewery production batches.';
COMMENT ON COLUMN cellar.brew.brew_id IS 'Sequential invented brew key.';
COMMENT ON COLUMN cellar.brew.brewed_on IS 'Date the invented batch was brewed.';
COMMENT ON COLUMN cellar.brew.style_code IS 'Invented internal style category.';
COMMENT ON COLUMN cellar.brew.ingredient_cost_gbp IS 'Invented ingredient cost in pounds sterling.';
CREATE TABLE cellar.keg (
  keg_id INTEGER PRIMARY KEY,
  brew_id INTEGER REFERENCES cellar.brew(brew_id),
  filled_on DATE NOT NULL,
  capacity_litres DECIMAL(10,2) NOT NULL,
  transfer_value_gbp DECIMAL(12,2) NOT NULL
);
COMMENT ON TABLE cellar.keg IS 'Invented filled kegs held in the cellar.';
COMMENT ON COLUMN cellar.keg.keg_id IS 'Sequential invented keg key.';
COMMENT ON COLUMN cellar.keg.brew_id IS 'Invented brew batch placed in the keg.';
COMMENT ON COLUMN cellar.keg.filled_on IS 'Date the invented keg was filled.';
COMMENT ON COLUMN cellar.keg.capacity_litres IS 'Invented keg capacity in litres.';
COMMENT ON COLUMN cellar.keg.transfer_value_gbp IS 'Invented internal transfer value in pounds sterling.';
