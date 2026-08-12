# Synthetic EPDK schema fixture

`synthetic_response.json` is project-authored synthetic data. It mirrors only the field names and
container shapes observed from the EPDK charging-station endpoint on 2026-08-09. It is not a copied
EPDK station response and makes no claim about real stations, availability, status, or freshness.

The fixture deliberately includes an invalid coordinate, invalid socket power, duplicate site, and
duplicate socket so reconciliation behavior remains deterministic and offline-testable.
