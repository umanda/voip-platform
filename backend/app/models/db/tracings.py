from enum import IntEnum

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, Text
from sqlalchemy.sql import func

from app.models.db.base import Base


class TracingStatus(IntEnum):
    """
    Event type codes for the tracings table.

    These are the exact integer values used by the legacy Sentinel system.
    Append-only — never update a tracing row (R-BILL-02).
    """

    START_TIME = 1
    RINGING_START_TIME = 3
    CONNECTED_TIME = 4
    CREDIT_BLOCK_UPDATED = 5
    HANGUP_TIME = 6
    END_TIME = 7
    DESTINATION_NUMBER_INVALID = 8
    INVALID_AUTH_ATTEMPT = 9
    CUSTOMER_AUTHENTICATED = 10
    CONSULTANT_AUTHENTICATED = 11
    CONSULTANT_CHANGE_IVR_STATUS = 12
    ENABLE_DIRECT_DIAL = 13


class Tracing(Base):
    """
    galaxy_2.tracings — append-only event log. Multiple rows per call.

    Every state transition during a call creates a new row. Never updated.
    This is a regulatory/audit requirement (R-BILL-02).

    The billing reconciliation at hangup reads the CUSTOMER_AUTHENTICATED (10)
    tracing to find the initial block deduction timestamp and the latest
    CREDIT_BLOCK_UPDATED (5) to find the last renewal.
    """

    __tablename__ = "tracings"

    id = Column(Integer, primary_key=True)
    statistics_id = Column(Integer, ForeignKey("statistics.id"), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    status = Column(Integer, nullable=False)   # TracingStatus int code
    info = Column(Text, nullable=True)
    credit_before = Column(Numeric(10, 5), nullable=True)
    credit_after = Column(Numeric(10, 5), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
