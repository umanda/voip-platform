from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.models.db.base import Base


class SiteIvrNumber(Base):
    """
    galaxy_2.site_ivr_numbers — DID/IVR number registry.

    number is stored WITHOUT the leading '+'. The legacy fixCliNumbers()
    strips '+' from both src and dst before any DB lookup or API call.
    The new Lua scripts must normalize to the same format.

    type_id: 1=site/credit, 2=direct dial, 3=service-desk, 4=coach portal, 5=premium.
    language_id: 1=nl, 2=fr, 3=es, 4=en.
    """

    __tablename__ = "site_ivr_numbers"

    id = Column(Integer, primary_key=True)
    number = Column(String, nullable=False, unique=True, index=True)
    type_id = Column(Integer, nullable=False)
    language_id = Column(Integer, nullable=False)
    country_id = Column(Integer, ForeignKey("countries.id"), nullable=True)
    group_id = Column(Integer, nullable=True)

    country = relationship("Country", lazy="select")
