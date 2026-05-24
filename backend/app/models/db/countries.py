from sqlalchemy import Boolean, Column, Integer, Numeric, String

from app.models.db.base import Base


class Country(Base):
    """
    galaxy_2.countries — per-country VAT rate, currency, and feature flags.
    Referenced by site_ivr_numbers and credits_customers (via user.country).
    """

    __tablename__ = "countries"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    currency_code = Column(String(10), nullable=False)
    # Sentinel default: 20% (from Configs/config.php default_vat_rate)
    effective_vat_rate = Column(Numeric(5, 2), nullable=False, default=20)
    # When false: customers in this country cannot use direct-dial numbers
    direct_number_enabled = Column(Boolean, nullable=False, default=False)
