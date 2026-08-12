# Fixture browser smoke — 2026-08-12

This local acceptance check exercised the packaged fixture-first browser flow at
`http://127.0.0.1:8765` with no OSRM or EPDK provider request.

## Verified interaction

- The initial page exposed the fixed synthetic endpoints, bounded vehicle inputs, map fallback
  surface, and disabled-until-valid route action.
- Selecting **Compare routes** returned four selectable strategy tabs: Fastest, Shortest distance,
  Fewest charging stops, and Fixed 80%.
- Selecting Shortest distance updated its tab state. Pressing ArrowRight then selected Fewest
  charging stops, confirming the documented keyboard tab interaction.
- The rendered fastest option showed `Origin -> Fast Hub -> Destination`, 205 modeled minutes, a
  10% arrival estimate, one charging stop, and the static-data model boundary.
- The browser console recorded no error-level messages during the interaction.

This is local UI acceptance evidence. The deterministic Python test suite still owns the offline
algorithm and HTTP-contract regression checks.
