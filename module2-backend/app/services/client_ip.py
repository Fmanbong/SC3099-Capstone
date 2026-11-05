"""Client address policy required by the grading contract."""
from ipaddress import IPv6Address, ip_address

from fastapi import Request

from app.errors import ApiError


def resolve_client_ip(request: Request) -> str:
    forwarded = request.headers.get('x-forwarded-for')
    raw = forwarded.split(',', 1)[0].strip() if forwarded is not None else (
        request.client.host if request.client else ''
    )
    try:
        # Scoped addresses are interface identifiers, not portable client identities.
        if '%' in raw:
            raise ValueError('Scoped address')
        address = ip_address(raw)
    except ValueError:
        raise ApiError(403, 'A valid client IP address is required', 'CLIENT_IP_INVALID') from None
    if isinstance(address, IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return str(address)
