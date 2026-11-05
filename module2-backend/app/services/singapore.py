"""Offline Singapore country and land-boundary policy (no network lookups)."""
from bisect import bisect_right
from functools import lru_cache
import hashlib
from ipaddress import ip_address, ip_network, IPv6Address
import json
import math
from pathlib import Path

from app.errors import ApiError
from app.services.client_ip import resolve_client_ip


_LOCAL_NETWORKS = tuple(map(ip_network, (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8',
    '169.254.0.0/16', '::1/128', 'fc00::/7', 'fe80::/10',
)))


class SingaporePolicy:
    def __init__(self, data_dir: Path | None = None):
        directory = data_dir if data_dir is not None else Path(__file__).resolve().parents[1] / 'data'
        try:
            manifest = json.loads((directory / 'manifest.json').read_text())
            if manifest['schema_version'] != 1:
                raise ValueError('Unknown data version')
            documents = {}
            for name in ('singapore-ip-ranges.json', 'singapore-boundary.geojson'):
                raw = (directory / name).read_bytes()
                if hashlib.sha256(raw).hexdigest() != manifest['files'][name]:
                    raise ValueError('Offline data checksum mismatch')
                documents[name] = json.loads(raw)
            self._ranges = {}
            self._starts = {}
            for version in (4, 6):
                rows = documents['singapore-ip-ranges.json'][f'ipv{version}']
                values = [(int(a), int(b)) for a, b in rows]
                previous = -1
                for start, end in values:
                    if not 0 <= start <= end < 2 ** (32 if version == 4 else 128) or start <= previous:
                        raise ValueError('Invalid IP range index')
                    previous = end
                if not values:
                    raise ValueError('Missing IP family')
                self._ranges[version] = values
                self._starts[version] = [a for a, _ in values]
            boundary = documents['singapore-boundary.geojson']
            if boundary['type'] != 'FeatureCollection':
                raise ValueError('Invalid boundary collection')
            self._polygons = []
            for feature in boundary['features']:
                geometry = feature['geometry']
                if geometry['type'] == 'Polygon':
                    polygons = [geometry['coordinates']]
                elif geometry['type'] == 'MultiPolygon':
                    polygons = geometry['coordinates']
                else:
                    raise ValueError('Boundary must contain polygons')
                for polygon in polygons:
                    if not polygon:
                        raise ValueError('Empty polygon')
                    for ring in polygon:
                        if len(ring) < 4 or ring[0] != ring[-1]:
                            raise ValueError('Invalid polygon ring')
                        for x, y in ring:
                            if not (math.isfinite(x) and math.isfinite(y) and -180 <= x <= 180 and -90 <= y <= 90):
                                raise ValueError('Invalid boundary coordinate')
                    self._polygons.append(polygon)
            if not self._polygons:
                raise ValueError('Missing country boundary')
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            raise ApiError(503, 'Offline location data is unavailable or invalid', 'REGION_DATA_UNAVAILABLE') from None

    def allows_ip(self, address: str) -> bool:
        try:
            if '%' in address:
                return False
            ip = ip_address(address)
        except ValueError:
            return False
        if isinstance(ip, IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if any(ip.version == network.version and ip in network for network in _LOCAL_NETWORKS):
            return True
        if not ip.is_global or ip.is_multicast:
            return False
        numeric = int(ip)
        index = bisect_right(self._starts[ip.version], numeric) - 1
        return index >= 0 and numeric <= self._ranges[ip.version][index][1]

    def allows_coordinates(self, latitude: float, longitude: float) -> bool:
        if not (math.isfinite(latitude) and math.isfinite(longitude)
                and -90 <= latitude <= 90 and -180 <= longitude <= 180):
            return False
        for outer, *holes in self._polygons:
            if _in_ring(longitude, latitude, outer) and not any(
                _in_ring(longitude, latitude, hole) for hole in holes
            ):
                return True
        return False

    def require_singapore(self, request, latitude: float, longitude: float) -> str:
        address = resolve_client_ip(request)
        if not self.allows_coordinates(latitude, longitude):
            raise ApiError(403, 'Check-ins require a location in Singapore', 'GPS_OUTSIDE_SINGAPORE')
        if not self.allows_ip(address):
            raise ApiError(403, 'Check-ins require a Singapore or local client IP', 'IP_OUTSIDE_SINGAPORE')
        return address


def _in_ring(x, y, ring):
    """Ray casting; points exactly on a land edge count as inside."""
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        if abs(cross) <= 1e-12 and min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2):
            return True
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


@lru_cache(maxsize=1)
def default_policy() -> SingaporePolicy:
    # Data updates take effect on process restart, not partway through a request.
    return SingaporePolicy()
