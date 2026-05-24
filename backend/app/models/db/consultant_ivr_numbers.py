from sqlalchemy import Column, ForeignKey, Integer
from sqlalchemy.orm import relationship

from app.models.db.base import Base


class ConsultantIvrNumber(Base):
    """
    galaxy_2.consultant_ivr_numbers — many-to-one: DIDs → consultant.

    Joins site_ivr_numbers to consultants. For type 2 (direct dial) and
    type 4 (coach portal), this is how a dialed DID maps to a specific coach.
    """

    __tablename__ = "consultant_ivr_numbers"

    id = Column(Integer, primary_key=True)
    consultant_id = Column(Integer, ForeignKey("consultants.id"), nullable=False)
    site_ivr_number_id = Column(Integer, ForeignKey("site_ivr_numbers.id"), nullable=False)
    group_id = Column(Integer, nullable=True)

    consultant = relationship("Consultant", lazy="select")
    site_ivr_number = relationship("SiteIvrNumber", lazy="select")
