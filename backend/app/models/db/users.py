from sqlalchemy import Boolean, Column, Integer

from app.models.db.base import Base


class User(Base):
    """
    galaxy_2.users — base user account shared by customers and consultants.
    Reverse-engineered from Eloquent model and BaseRepository usage.
    ACTION: validate is_deleted column type against live DB (could be tinyint(1)).
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    status = Column(Integer, nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False)
    group_id = Column(Integer, nullable=True)
