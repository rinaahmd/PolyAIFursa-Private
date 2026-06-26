from sqlalchemy import Column, ForeignKey, Integer, String, Float, DateTime, text
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class PredictionSession(Base):
    __tablename__ = "prediction_sessions"

    uid = Column(String, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False)
    original_image = Column(String, nullable=False)
    predicted_image = Column(String, nullable=False)

    detection_objects = relationship(
        "DetectionObject",
        back_populates="prediction_session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DetectionObject(Base):
    __tablename__ = "detection_objects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    prediction_uid = Column(String, ForeignKey("prediction_sessions.uid", ondelete="CASCADE"), nullable=False, index=True)
    label = Column(String, nullable=False, index=True)
    score = Column(Float, nullable=False, index=True)
    box = Column(String, nullable=False)

    prediction_session = relationship("PredictionSession", back_populates="detection_objects")
