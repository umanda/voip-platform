from sqlalchemy import Column, ForeignKey, Integer, String

from app.models.db.base import Base


class ConsultantExtensionDetail(Base):
    """
    galaxy_2.consultant_extension_details — 4-digit extension codes per consultant.

    Customers can dial a site number and enter a 4-digit code to reach
    a specific consultant without knowing their direct DID.
    """

    __tablename__ = "consultant_extension_details"

    id = Column(Integer, primary_key=True)
    consultant_id = Column(Integer, ForeignKey("consultants.id"), nullable=False)
    group_id = Column(Integer, nullable=True)
    extension = Column(String(4), nullable=False)
