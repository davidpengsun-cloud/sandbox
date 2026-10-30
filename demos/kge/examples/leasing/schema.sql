-- Asset Finance: an invented business unit of Meridian Union Bank, for the onboarding demo (README, Demo script step 10).
-- Everything here is invented: tables, keys and comments, and no rows (onboarding invents them, labelled so).
CREATE SCHEMA leasing;
CREATE TABLE leasing.lessee (lessee_id INTEGER PRIMARY KEY, sector_code VARCHAR NOT NULL, signed_on DATE NOT NULL);
CREATE TABLE leasing.lease (lease_id INTEGER PRIMARY KEY, lessee_id INTEGER NOT NULL REFERENCES leasing.lessee (lessee_id),
                            equipment_type VARCHAR NOT NULL, started_on DATE NOT NULL);
CREATE TABLE leasing.rental (rental_id INTEGER PRIMARY KEY, lease_id INTEGER NOT NULL REFERENCES leasing.lease (lease_id),
                             billed_on DATE NOT NULL, lease_rentals_usd DECIMAL(12,2) NOT NULL);
COMMENT ON TABLE leasing.lessee IS 'Businesses that lease equipment from Asset Finance';
COMMENT ON COLUMN leasing.lessee.lessee_id IS 'Lessee identifier';
COMMENT ON COLUMN leasing.lessee.sector_code IS 'Industry sector: LOGISTICS, CONSTRUCTION or HEALTHCARE';
COMMENT ON COLUMN leasing.lessee.signed_on IS 'Date the master lease agreement was signed';
COMMENT ON TABLE leasing.lease IS 'One lease of one item of equipment to one lessee';
COMMENT ON COLUMN leasing.lease.lease_id IS 'Lease identifier';
COMMENT ON COLUMN leasing.lease.lessee_id IS 'Lessee holding the lease';
COMMENT ON COLUMN leasing.lease.equipment_type IS 'Equipment leased: VEHICLE, PLANT or IT';
COMMENT ON COLUMN leasing.lease.started_on IS 'Date the lease started';
COMMENT ON TABLE leasing.rental IS 'Rentals billed to lessees';
COMMENT ON COLUMN leasing.rental.rental_id IS 'Rental identifier';
COMMENT ON COLUMN leasing.rental.lease_id IS 'Lease billed';
COMMENT ON COLUMN leasing.rental.billed_on IS 'Date the rental was billed';
COMMENT ON COLUMN leasing.rental.lease_rentals_usd IS 'Lease rentals billed, in USD; the business calls this lease income';
