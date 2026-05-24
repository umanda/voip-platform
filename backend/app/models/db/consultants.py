from sqlalchemy import Boolean, Column, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import relationship

from app.models.db.base import Base


class Consultant(Base):
    """
    galaxy_2.consultants — coach/consultant profile.

    ivr_status: 1=online (available), 2=busy (in call), 3=offline.
    Set to 2 on CHANNEL_ANSWER, reset to 1 on hangup billing completion.
    Risk: stays stuck at 2 if billing fails (HIGH-04 in risk-findings.md).

    provider_sequence: pipe-separated gateway list, e.g. "voxbone-outbound|provider2".
    Only the first is currently active — providers 2 and 3 are commented out in legacy.
    """

    __tablename__ = "consultants"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    ivr_status = Column(Integer, nullable=False, default=3)
    call_rate = Column(Numeric(10, 5), nullable=False)
    currency_code = Column(String(10), nullable=False)
    commission_percentage = Column(Numeric(5, 2), nullable=False, default=0)
    is_blocked = Column(Boolean, nullable=False, default=False)
    is_deleted = Column(Boolean, nullable=False, default=False)
    provider_sequence = Column(String, nullable=True)

    user = relationship("User", lazy="select")
    phone_numbers = relationship("ConsultantPhoneNumber", lazy="select")
    extension_details = relationship("ConsultantExtensionDetail", lazy="select")
