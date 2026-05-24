from sqlalchemy import Column, Integer, String

from app.models.db.base import Base


class IvrKnownUser(Base):
    """
    galaxy_2.ivr_known_users — phone number → user mapping for direct-dial pre-auth.

    When a customer saves their PIN via the IVR, their src_number is stored here.
    On subsequent calls from the same number, PIN entry is skipped (direct_dial_auth).

    KNOWN GAP: No TTL or expiry on these records (risk-findings.md MED-03).
    ACTION: validate live schema — id column may not exist; number may be the PK.
    """

    __tablename__ = "ivr_known_users"

    id = Column(Integer, primary_key=True)
    caller_id = Column(String, nullable=False, index=True)
    credit_code = Column(String(8), nullable=False)
