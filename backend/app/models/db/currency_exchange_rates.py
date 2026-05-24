from sqlalchemy import Column, DateTime, Integer, JSON
from sqlalchemy.sql import func

from app.models.db.base import Base


class CurrencyExchangeRate(Base):
    """
    galaxy_2.currency_exchange_rates — FX rates stored as a JSON blob.

    The legacy system always uses the LATEST row (latest()->first() in PHP).
    This table is populated by the main Helios scheduler, not Sentinel.
    ACTION: inspect live DB to confirm rates JSON structure before using get_fx_rate().
    """

    __tablename__ = "currency_exchange_rates"

    id = Column(Integer, primary_key=True)
    # JSON blob: structure needs live DB validation.
    # Expected: {currency_code: float} or {currency_code: {rate: float}}
    rates = Column(JSON, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
