from sqlalchemy import Boolean, Column, ForeignKey, Integer, Numeric, String

from app.models.db.base import Base


class ConsultantPhoneNumber(Base):
    """
    galaxy_2.consultant_phone_numbers — PSTN numbers used for outbound dial.

    The active phone number is dialed by FreeSWITCH via the Voxbone gateway.
    surcharge_amount is added to credit deduction for numbers with a per-call fee.
    """

    __tablename__ = "consultant_phone_numbers"

    id = Column(Integer, primary_key=True)
    consultant_id = Column(Integer, ForeignKey("consultants.id"), nullable=False)
    phone_number = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    surcharge_amount = Column(Numeric(10, 5), nullable=False, default=0)
