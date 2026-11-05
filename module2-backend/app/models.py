"""Persistent resources. Biometric images and raw embeddings have no columns."""
import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, event

from app.database import Base, UTCDateTime, utcnow


def identifier():
    return str(uuid.uuid4())


class Identified:
    id = Column(String(36), primary_key=True, default=identifier)


class Timestamps:
    created_at = Column(UTCDateTime, default=utcnow, nullable=False)
    updated_at = Column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


class User(Identified, Timestamps, Base):
    __tablename__ = 'users'
    __table_args__ = (
        CheckConstraint("role IN ('student','ta','instructor','admin')"),
        CheckConstraint("face_enrollment_status IN ('none','pending','approved','rejected')",
                        name='ck_users_face_enrollment_status'),
    )
    email = Column(String(255), unique=True, nullable=False)
    full_name = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    failed_login_attempts = Column(Integer, default=0, server_default='0', nullable=False)
    role = Column(String(20), default='student', nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    camera_consent = Column(Boolean, default=False, nullable=False)
    geolocation_consent = Column(Boolean, default=False, nullable=False)
    face_embedding_hash = Column(String(256))
    face_enrolled = Column(Boolean, default=False, nullable=False)
    # Staff approval of face enrollments (FACE_ENROLLMENT_APPROVAL). The approved
    # template stays in face_embedding_hash; a new one waits in pending_face_hash.
    face_enrollment_status = Column(String(20), default='none', nullable=False, index=True)
    pending_face_hash = Column(String(256))
    face_enrollment_requested_at = Column(UTCDateTime)
    face_enrollment_reviewed_by_id = Column(String(36), ForeignKey('users.id', ondelete='SET NULL'))
    face_enrollment_reviewed_at = Column(UTCDateTime)
    face_enrollment_review_notes = Column(Text)
    last_login_at = Column(UTCDateTime)
    scheduled_deletion_at = Column(UTCDateTime, index=True)


class Course(Identified, Timestamps, Base):
    __tablename__ = 'courses'
    code = Column(String(20), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    semester = Column(String(100), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    venue_latitude = Column(Float)
    venue_longitude = Column(Float)
    venue_name = Column(String(255))
    geofence_radius_meters = Column(Float, default=100.0, nullable=False)
    require_face_recognition = Column(Boolean, default=False, nullable=False)
    require_device_binding = Column(Boolean, default=True, nullable=False)
    risk_threshold = Column(Float, default=0.5, nullable=False)


class CourseStaff(Identified, Base):
    __tablename__ = 'course_staff'
    __table_args__ = (UniqueConstraint('course_id', 'user_id'),)
    course_id = Column(String(36), ForeignKey('courses.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)


class Enrollment(Identified, Base):
    __tablename__ = 'enrollments'
    __table_args__ = (UniqueConstraint('student_id', 'course_id', name='uq_enrollment_student_course'),)
    student_id = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    course_id = Column(String(36), ForeignKey('courses.id', ondelete='CASCADE'), nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    enrolled_at = Column(UTCDateTime, default=utcnow, nullable=False)
    dropped_at = Column(UTCDateTime)


class EnrollmentPeriod(Identified, Base):
    """Closed enrollment intervals archived before reactivation replaces dates."""
    __tablename__ = 'enrollment_periods'
    __table_args__ = (UniqueConstraint('enrollment_id', 'started_at'),)
    enrollment_id = Column(String(36), ForeignKey('enrollments.id', ondelete='CASCADE'), nullable=False, index=True)
    started_at = Column(UTCDateTime, nullable=False)
    ended_at = Column(UTCDateTime, nullable=False)


class Session(Identified, Timestamps, Base):
    __tablename__ = 'sessions'
    __table_args__ = (CheckConstraint("status IN ('scheduled','active','closed','cancelled')"),)
    course_id = Column(String(36), ForeignKey('courses.id', ondelete='CASCADE'), nullable=False, index=True)
    instructor_id = Column(String(36), ForeignKey('users.id', ondelete='SET NULL'), index=True)
    name = Column(String(255), nullable=False)
    session_type = Column(String(50), default='lecture', nullable=False)
    description = Column(Text)
    scheduled_start = Column(UTCDateTime, nullable=False, index=True)
    scheduled_end = Column(UTCDateTime, nullable=False)
    checkin_opens_at = Column(UTCDateTime, nullable=False, index=True)
    checkin_closes_at = Column(UTCDateTime, nullable=False, index=True)
    status = Column(String(20), default='scheduled', nullable=False, index=True)
    actual_start = Column(UTCDateTime)
    actual_end = Column(UTCDateTime)
    venue_latitude = Column(Float)
    venue_longitude = Column(Float)
    venue_name = Column(String(255))
    geofence_radius_meters = Column(Float)
    require_liveness_check = Column(Boolean, default=True, nullable=False)
    require_face_match = Column(Boolean, default=False, nullable=False)
    risk_threshold = Column(Float)
    qr_code_secret = Column(String(128))
    qr_code_expires_at = Column(UTCDateTime)


class Device(Identified, Base):
    __tablename__ = 'devices'
    user_id = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    device_fingerprint = Column(String(128), unique=True, nullable=False)
    device_name = Column(String(255))
    platform = Column(String(50))
    browser = Column(String(100))
    os_version = Column(String(100))
    app_version = Column(String(50))
    public_key = Column(Text)
    public_key_created_at = Column(UTCDateTime)
    public_key_expires_at = Column(UTCDateTime)
    is_trusted = Column(Boolean, default=False, nullable=False)
    trust_score = Column(String(20), default='low', nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    first_seen_at = Column(UTCDateTime, default=utcnow, nullable=False)
    last_seen_at = Column(UTCDateTime, default=utcnow, nullable=False)
    total_checkins = Column(Integer, default=0, nullable=False)
    revoked_at = Column(UTCDateTime)
    revocation_reason = Column(Text)
    attestation_passed = Column(Boolean, default=False, nullable=False)
    last_attestation_at = Column(UTCDateTime)
    is_emulator = Column(Boolean, default=False, nullable=False)
    is_rooted_jailbroken = Column(Boolean, default=False, nullable=False)


class DeviceChallenge(Identified, Base):
    __tablename__ = 'device_challenges'
    user_id = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    device_id = Column(String(36), ForeignKey('devices.id', ondelete='CASCADE'), nullable=False)
    session_id = Column(String(36), ForeignKey('sessions.id', ondelete='CASCADE'), nullable=False)
    nonce = Column(String(128), nullable=False, unique=True)
    expires_at = Column(UTCDateTime, nullable=False, index=True)
    consumed_at = Column(UTCDateTime)


class CheckIn(Identified, Base):
    __tablename__ = 'checkins'
    __table_args__ = (
        UniqueConstraint('session_id', 'student_id', name='uq_checkin_session_student'),
        CheckConstraint("status IN ('pending','approved','flagged','rejected','appealed')"),
    )
    session_id = Column(String(36), ForeignKey('sessions.id', ondelete='CASCADE'), nullable=False, index=True)
    student_id = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    device_id = Column(String(36), ForeignKey('devices.id', ondelete='SET NULL'))
    status = Column(String(20), default='pending', nullable=False, index=True)
    checked_in_at = Column(UTCDateTime, default=utcnow, nullable=False, index=True)
    verified_at = Column(UTCDateTime)
    latitude = Column(Float)
    longitude = Column(Float)
    location_accuracy_meters = Column(Float)
    distance_from_venue_meters = Column(Float)
    liveness_passed = Column(Boolean)
    liveness_score = Column(Float)
    liveness_challenge_type = Column(String(50))
    face_match_passed = Column(Boolean)
    face_match_score = Column(Float)
    face_embedding_hash = Column(String(256))
    risk_score = Column(Float, default=0.0, nullable=False, index=True)
    risk_factors = Column(JSON, default=list, nullable=False)
    qr_code_verified = Column(Boolean, default=False, nullable=False)
    reviewed_by_id = Column(String(36), ForeignKey('users.id', ondelete='SET NULL'))
    reviewed_at = Column(UTCDateTime)
    review_notes = Column(Text)
    appeal_reason = Column(Text)
    appealed_at = Column(UTCDateTime)
    scheduled_deletion_at = Column(UTCDateTime, index=True)


class RiskSignal(Identified, Base):
    __tablename__ = 'risk_signals'
    checkin_id = Column(String(36), ForeignKey('checkins.id', ondelete='CASCADE'), nullable=False, index=True)
    signal_type = Column(String(100), nullable=False)
    score = Column(Float, nullable=False)
    weight = Column(Float, nullable=False)
    details = Column(JSON, default=dict, nullable=False)
    created_at = Column(UTCDateTime, default=utcnow, nullable=False)


class AuditLog(Identified, Base):
    __tablename__ = 'audit_logs'
    user_id = Column(String(36), ForeignKey('users.id', ondelete='SET NULL'), index=True)
    action = Column(String(100), nullable=False, index=True)
    resource_type = Column(String(100))
    resource_id = Column(String(36))
    details = Column(JSON, default=dict, nullable=False)
    ip_address = Column(String(64))
    user_agent = Column(String(512))
    severity = Column(String(20), default='info', nullable=False)
    created_at = Column(UTCDateTime, default=utcnow, nullable=False, index=True)


@event.listens_for(AuditLog, 'before_update')
@event.listens_for(AuditLog, 'before_delete')
def immutable_audit(mapper, connection, target):
    raise ValueError('Audit entries are append-only')


class RefreshSession(Identified, Base):
    __tablename__ = 'refresh_sessions'
    user_id = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    family_id = Column(String(36), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    expires_at = Column(UTCDateTime, nullable=False, index=True)
    created_at = Column(UTCDateTime, default=utcnow, nullable=False)
    revoked_at = Column(UTCDateTime)
    used_at = Column(UTCDateTime)
