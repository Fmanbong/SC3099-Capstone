"""Build the offline SG policy from downloaded, pinned public source files.

No downloads occur in this script or the runtime service. See app/data/NOTICE.md.
"""
import argparse
import csv
import gzip
import hashlib
from ipaddress import ip_address
import json
from pathlib import Path
import re


def build(country_csv: Path, boundary: Path, release: str, output: Path) -> None:
    if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])', release):
        raise ValueError('Release must be YYYY-MM')
    ranges = {4: [], 6: []}
    opener = gzip.open if country_csv.suffix == '.gz' else open
    with opener(country_csv, 'rt', encoding='utf-8', newline='') as stream:
        for start, end, country in csv.reader(stream):
            if country != 'SG':
                continue
            first, last = ip_address(start), ip_address(end)
            if first.version != last.version or first > last:
                raise ValueError('Invalid country range')
            ranges[first.version].append((int(first), int(last)))
    index = {'release': release, 'ipv4': [], 'ipv6': []}
    for version, rows in ranges.items():
        if not rows:
            raise ValueError(f'No Singapore IPv{version} data')
        merged = []
        for start, end in sorted(rows):
            if merged and start <= merged[-1][1] + 1:
                merged[-1][1] = max(end, merged[-1][1])
            else:
                merged.append([start, end])
        # Decimal strings preserve 128-bit values in all JSON readers.
        index[f'ipv{version}'] = [[str(a), str(b)] for a, b in merged]
    geometry = json.loads(boundary.read_text())
    if geometry.get('type') != 'FeatureCollection' or not geometry.get('features'):
        raise ValueError('Boundary must be a GeoJSON FeatureCollection')
    payloads = {
        'singapore-ip-ranges.json': json.dumps(index, separators=(',', ':')).encode() + b'\n',
        'singapore-boundary.geojson': boundary.read_bytes(),
    }
    manifest = {
        'schema_version': 1,
        'files': {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()},
        'ip_source': {
            'url': f'https://download.db-ip.com/free/dbip-country-lite-{release}.csv.gz',
            'sha256': hashlib.sha256(country_csv.read_bytes()).hexdigest(),
            'release': release, 'license': 'CC-BY-4.0', 'attribution': 'IP Geolocation by DB-IP.com',
        },
        'boundary_source': {
            'url': 'https://www.geoboundaries.org/api/current/gbOpen/SGP/ADM0/',
            'download_url': 'https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/SGP/ADM0/geoBoundaries-SGP-ADM0.geojson',
            'boundary_year': '2016',
            'sha256': hashlib.sha256(boundary.read_bytes()).hexdigest(),
            'license': 'ODbL-1.0',
            'attribution': 'geoBoundaries; Urban Redevelopment Authority, Singapore',
        },
    }
    # Validate the entire generated bundle before replacing an existing release.
    import sys
    import tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.services.singapore import SingaporePolicy
    with tempfile.TemporaryDirectory() as staging:
        staged = Path(staging)
        for name, data in payloads.items():
            (staged / name).write_bytes(data)
        (staged / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        SingaporePolicy(staged)
        output.mkdir(parents=True, exist_ok=True)
        for name in (*payloads, 'manifest.json'):
            temporary = output / (name + '.tmp')
            temporary.write_bytes((staged / name).read_bytes())
            temporary.replace(output / name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--country-csv', required=True, type=Path)
    parser.add_argument('--boundary', required=True, type=Path)
    parser.add_argument('--release', required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'app/data')
    args = parser.parse_args()
    build(args.country_csv, args.boundary, args.release, args.output)
    print(f'Validated offline Singapore data written to {args.output}; restart backend after updates.')


if __name__ == '__main__':
    main()
