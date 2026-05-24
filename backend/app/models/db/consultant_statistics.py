from sqlalchemy import Column, ForeignKey, Integer

from app.models.db.base import Base


class ConsultantStatistics(Base):
    """
    galaxy_2.consultant_statistics — aggregate call stats per consultant.

    Updated at hangup by the billing reconciliation step.
    no_of_consultations and total_call_duration are running totals.
    """

    __tablename__ = "consultant_statistics"

    id = Column(Integer, primary_key=True)
    consultant_id = Column(Integer, ForeignKey("consultants.id"), nullable=False)
    group_id = Column(Integer, nullable=True)
    no_of_consultations = Column(Integer, nullable=False, default=0)
    total_call_duration = Column(Integer, nullable=False, default=0)
