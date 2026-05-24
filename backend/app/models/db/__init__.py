# Import all models so SQLAlchemy's metadata is fully populated.
# Required for Alembic autogenerate and for relationship resolution.
from app.models.db.base import Base
from app.models.db.consultant_extension_details import ConsultantExtensionDetail
from app.models.db.consultant_ivr_numbers import ConsultantIvrNumber
from app.models.db.consultant_phone_numbers import ConsultantPhoneNumber
from app.models.db.consultant_statistics import ConsultantStatistics
from app.models.db.consultants import Consultant
from app.models.db.countries import Country
from app.models.db.credits_customers import CreditsCustomer
from app.models.db.currency_exchange_rates import CurrencyExchangeRate
from app.models.db.ivr_known_users import IvrKnownUser
from app.models.db.site_ivr_numbers import SiteIvrNumber
from app.models.db.statistics import Statistics
from app.models.db.tracings import Tracing, TracingStatus
from app.models.db.users import User

__all__ = [
    "Base",
    "Consultant",
    "ConsultantExtensionDetail",
    "ConsultantIvrNumber",
    "ConsultantPhoneNumber",
    "ConsultantStatistics",
    "Country",
    "CreditsCustomer",
    "CurrencyExchangeRate",
    "IvrKnownUser",
    "SiteIvrNumber",
    "Statistics",
    "Tracing",
    "TracingStatus",
    "User",
]
