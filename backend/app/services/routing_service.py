import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DIDNotFoundError
from app.models.db.consultant_ivr_numbers import ConsultantIvrNumber
from app.models.db.consultant_phone_numbers import ConsultantPhoneNumber
from app.models.db.consultants import Consultant
from app.models.db.ivr_known_users import IvrKnownUser
from app.models.db.site_ivr_numbers import SiteIvrNumber

logger = structlog.get_logger(__name__)


async def lookup_did(
    db: AsyncSession,
    inbound_did: str,
) -> SiteIvrNumber:
    """
    Resolve an inbound DID to its site_ivr_numbers row.

    The DB stores numbers WITHOUT the leading '+'. The legacy fixCliNumbers()
    in checkServiceType.pl stripped '+' before every API call. The new Lua
    scripts must do the same normalization before calling this endpoint.

    Args:
        inbound_did: DID as received — may include '+' prefix.

    Returns:
        SiteIvrNumber with type_id, language_id, country_id.

    Raises:
        DIDNotFoundError: Number not in site_ivr_numbers.
    """
    normalized = inbound_did.lstrip("+")
    result = await db.execute(
        select(SiteIvrNumber).where(SiteIvrNumber.number == normalized)
    )
    did = result.scalar_one_or_none()
    if did is None:
        raise DIDNotFoundError(f"DID {inbound_did} not in site_ivr_numbers")
    return did


async def get_consultant_for_did(
    db: AsyncSession,
    site_ivr_number_id: int,
) -> tuple[Consultant | None, ConsultantPhoneNumber | None]:
    """
    Resolve the consultant and their active outbound phone number for a DID.

    Relevant for service types 2 (direct dial) and 4 (coach portal).
    For type 1 (site/credit) and 3 (service-desk), no single consultant
    is linked to the DID — returns (None, None).

    Returns:
        (Consultant, ConsultantPhoneNumber) or (None, None).
    """
    mapping_result = await db.execute(
        select(ConsultantIvrNumber).where(
            ConsultantIvrNumber.site_ivr_number_id == site_ivr_number_id
        )
    )
    mapping = mapping_result.scalar_one_or_none()
    if mapping is None:
        return None, None

    consultant_result = await db.execute(
        select(Consultant).where(Consultant.id == mapping.consultant_id)
    )
    consultant = consultant_result.scalar_one_or_none()
    if consultant is None:
        return None, None

    phone_result = await db.execute(
        select(ConsultantPhoneNumber)
        .where(ConsultantPhoneNumber.consultant_id == consultant.id)
        .where(ConsultantPhoneNumber.is_active.is_(True))
        .limit(1)
    )
    phone = phone_result.scalar_one_or_none()
    return consultant, phone


async def lookup_known_user(
    db: AsyncSession,
    src_number: str,
) -> IvrKnownUser | None:
    """
    Check if the caller has a saved pre-auth mapping in ivr_known_users.

    In the legacy system, when a customer opted to save their number,
    their src_number → user_id mapping was stored here. Subsequent calls
    from the same number bypass PIN entry (direct_dial_auth = True).

    Numbers stored without '+' — strip before lookup (matches fixCliNumbers()).

    KNOWN GAP: No TTL on these records (risk-findings.md MED-03).
    """
    normalized = src_number.lstrip("+")
    result = await db.execute(
        select(IvrKnownUser).where(IvrKnownUser.caller_id == normalized)
    )
    return result.scalar_one_or_none()


_PROVIDER_SLOT_MAP = {
    "1": "voxbone-outbound",
    "2": "voxbone-outbound",
    "3": "voxbone-outbound",
}
_DEFAULT_GATEWAY = "voxbone-outbound"


def get_gateway_from_consultant(consultant: Consultant | None) -> str:
    """
    Resolve the outbound SIP gateway name from consultant.provider_sequence.

    Legacy behavior: provider_sequence stores slot indices "1|1|1", not names.
    The sofia.conf [providers] section maps these: provider1=voxbone-outbound.
    dialCoach.pl falls through to provider1 (voxbone-outbound) for all calls.

    If the field is eventually updated to store gateway names directly
    (e.g. "voxbone-outbound|backup-gw"), named values pass through unchanged.

    Returns:
        Gateway name used in FreeSWITCH dial string:
        sofia/gateway/<name>/+<number>
    """
    if consultant and consultant.provider_sequence:
        first = consultant.provider_sequence.split("|")[0].strip()
        return _PROVIDER_SLOT_MAP.get(first, first if not first.isdigit() else _DEFAULT_GATEWAY)
    return _DEFAULT_GATEWAY
