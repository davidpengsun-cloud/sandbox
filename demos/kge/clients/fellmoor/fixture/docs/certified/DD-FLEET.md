---
title: "Data dictionary: Fleet Engineering"
kind: data_dictionary
unit: fleet
owner: Fleet Data Steward
version: "2.0"
published_at: "2025-03-17"
reviewed_at: "2025-06-16"

---
The Fleet Engineering warehouse holds the fleet register and the repairs made to each vehicle. A vehicle runs from one depot, NORTHGATE or RIVERSIDE.

**Maintenance Cost** is the parts and labour charged to repairs completed in a period, in USD: `repair.cost_usd`, by the day in `repair.completed_on`, on the calendar year. A month's figure is the sum of the repairs completed in it. Work in progress is not here.

The fleet number in `vehicle.vehicle_no` is the register's own. Fares and Ticketing holds no fleet number it has checked, so the two warehouses share no key.
