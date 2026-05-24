from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.sql import func

from app.models.db.base import Base


class Statistics(Base):
    """
    galaxy_2.statistics — primary CDR table. One row per call.

    Created at call start (/call/validate), updated through lifecycle events,
    finalized at hangup. The same row is updated multiple times — this is
    NOT append-only (unlike tracings). FastAPI billing worker must replicate
    this lifecycle: create stub on authorize, finalize on CHANNEL_HANGUP_COMPLETE.

    Column names are preserved EXACTLY as in the legacy Laravel schema.
    Do NOT rename anything here — the billing worker reads/writes by column name.
    """

    __tablename__ = "statistics"

    id = Column(Integer, primary_key=True)
    # FreeSWITCH call UUID — the join key between Sofia ESL events and this row
    unique_id = Column(String, nullable=False, index=True)

    # FK references (all nullable — not all service types populate all fields)
    consultant_id = Column(Integer, ForeignKey("consultants.id"), nullable=True)
    credit_customer_id = Column(Integer, ForeignKey("credits_customers.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    group_id = Column(Integer, nullable=True)
    site_ivr_number_id = Column(Integer, ForeignKey("site_ivr_numbers.id"), nullable=False)

    # Call metadata
    provider_id = Column(Integer, nullable=False, default=1)
    type_id = Column(Integer, nullable=False)           # 1=site, 2=direct, 3=SD, 4=coach
    type = Column(String, nullable=False, default="call")
    src_number = Column(String, nullable=False)         # caller ID, '+' stripped
    dst_number = Column(String, nullable=False)         # coach's phone number
    extension = Column(Integer, nullable=False, default=0)

    # Call timestamps (populated progressively through the call lifecycle)
    start_time = Column(DateTime, nullable=False)
    ringing_start_time = Column(DateTime, nullable=True)
    connected_time = Column(DateTime, nullable=True)
    hangup_time = Column(DateTime, nullable=True)       # B-leg hangs up
    end_time = Column(DateTime, nullable=True)          # A-leg hangs up

    # Duration (seconds) — computed at hangup
    total_duration = Column(Integer, nullable=True)
    conversation_duration = Column(Integer, nullable=True)

    # Billing snapshot — all captured at call setup and finalized at hangup
    credit_before = Column(Numeric(10, 5), nullable=False, default=0)
    credit_after = Column(Numeric(10, 5), nullable=False, default=0)
    coach_rate = Column(Numeric(10, 5), nullable=False, default=0)
    vat_rate = Column(Numeric(5, 2), nullable=False, default=0)
    # Column name preserved exactly: consultant_earning_for_minute
    consultant_earning_for_minute = Column(Numeric(10, 5), nullable=False, default=0)
    consultant_total_earning = Column(Numeric(10, 5), nullable=True)
    credit_without_vat = Column(Numeric(10, 5), nullable=True)
    surcharge_amount = Column(Numeric(10, 5), nullable=True)
    allocated_vat_amount = Column(Numeric(10, 5), nullable=True)

    # Currency context (captured at call setup — rates may change, lock them in)
    customer_currency_code = Column(String(10), nullable=False, default="eur")
    customer_currency_rate = Column(Numeric(10, 5), nullable=False, default=1)
    coach_currency_code = Column(String(10), nullable=False, default="eur")
    coach_currency_rate = Column(Numeric(10, 5), nullable=False, default=1)
    company_and_coach_currency_rate = Column(Numeric(10, 5), nullable=True)
    company_and_customer_currency_rate = Column(Numeric(10, 5), nullable=True)

    # Final call disposition
    # Values: NORMAL / SHORT CALL / NO ANSWER / DISCONNECT /
    #         REMOTE BUSY / LOCAL_BUSY / EXTERNAL_BUSY / CUSTOMER_HANGUP_BEFORE_ANSWER
    status = Column(String, nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
