from sqlalchemy import Boolean, Column, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import relationship

from app.models.db.base import Base


class CreditsCustomer(Base):
    """
    galaxy_2.credits_customers — customer credit account.

    current_credits is the live balance field. In the new system it is
    shadowed by Redis key credit:{id} for atomic deduction (R-BILL-01).
    Postgres remains the settlement source of truth.

    credit_code is the 8-digit PIN entered by the customer via DTMF.
    It is the primary customer identifier in the IVR system.
    """

    __tablename__ = "credits_customers"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    credit_code = Column(String(8), nullable=False, unique=True, index=True)
    current_credits = Column(Numeric(10, 5), nullable=False, default=0)
    currency_code = Column(String(10), nullable=False, default="eur")
    is_blocked = Column(Boolean, nullable=False, default=False)
    is_deleted = Column(Boolean, nullable=False, default=False)

    user = relationship("User", lazy="select")
