from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class PredictionSession(Base):
    __tablename__ = "prediction_sessions"

    uid = Column(String, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    original_image = Column(String, nullable=False)
    predicted_image = Column(String, nullable=False)

    detection_objects = relationship(
        "DetectionObject",
        back_populates="prediction_session",
        cascade="all, delete-orphan"
    )


class DetectionObject(Base):
    __tablename__ = "detection_objects"

    id = Column(Integer, primary_key=True, index=True)
    prediction_uid = Column(String, ForeignKey("prediction_sessions.uid"), nullable=False, index=True)
    label = Column(String, nullable=False, index=True)
    score = Column(Float, nullable=False, index=True)
    box = Column(String, nullable=False)

    prediction_session = relationship("PredictionSession", back_populates="detection_objects")
