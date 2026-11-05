# Offline Singapore location data

This bundle is used for the Singapore-only check-in gate. Runtime requests do
not download files or contact a geolocation API. The backend image includes this
directory through its existing `COPY . .` instruction.

## IP Geolocation by DB-IP

Singapore-only IPv4/IPv6 ranges in `singapore-ip-ranges.json` are derived from
[DB-IP Country Lite](https://db-ip.com/db/lite.php), November 2026 release.
Copyright DB-IP.com; licensed under
[Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).
Changes: retain SG rows only, convert endpoints to decimal integers, sort and
merge overlapping/adjacent ranges. Nonmatching public IPs are denied, including
unmapped addresses; this is not a complete worldwide country database.

## Singapore land boundary

`singapore-boundary.geojson` is the unmodified
[geoBoundaries Singapore ADM0](https://www.geoboundaries.org/api/current/gbOpen/SGP/ADM0/)
file at upstream commit `9469f09`, boundary ID `SGP-ADM0-21272760`. Source:
Urban Redevelopment Authority, derived from ADM3, master-plan subzone land
boundaries. Geographic representation: 2016; upstream build: December 2023.
Licensed under [Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
This source database remains separately distributed under that license.

Pinned download (Git LFS media, not the raw pointer):
https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/SGP/ADM0/geoBoundaries-SGP-ADM0.geojson

## Updates

Run from `module2-backend` in an environment with its Python requirements.
Download source files to a temporary directory, review provenance/licenses, and
use the published DB-IP release date in both the URL and `--release` argument:

```bash
curl --fail --location https://download.db-ip.com/free/dbip-country-lite-2026-11.csv.gz --output /tmp/dbip-country-lite-2026-11.csv.gz
curl --fail --location https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/SGP/ADM0/geoBoundaries-SGP-ADM0.geojson --output /tmp/singapore-adm0.geojson
python scripts/update_singapore_data.py --country-csv /tmp/dbip-country-lite-2026-11.csv.gz --boundary /tmp/singapore-adm0.geojson --release 2026-11
python -m pytest tests/test_singapore.py -q
```

The builder validates input and generated data before replacing files. It records
source/output SHA-256 hashes and licenses in `manifest.json`. When changing the
boundary source, update this notice and the manifest's provenance in the builder.
Rebuild/restart the backend only after the complete update succeeds. Do not run
multiple builders against the same directory simultaneously. The runtime checks
checksums when loading data; a missing/invalid bundle prevents service startup.

## Accuracy limits

DB-IP Lite has reduced coverage/accuracy compared with its commercial database.
Public IP location reflects this monthly snapshot, not a live measurement. The
land polygon excludes sea and can reject recently reclaimed land because of the
older boundary source. It includes the source's islands and excludes polygon
holes. Update data and verify relevant campus locations before deployment.
Neither GPS nor a forwarded header proves a student's physical presence.
