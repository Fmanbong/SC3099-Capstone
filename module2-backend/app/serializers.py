"""Explicitly exclude secret fields at the output boundary."""
from sqlalchemy import inspect

from app.models import Course, Session, User


def fields(obj, excluded=()):
    return {col.key: getattr(obj, col.key) for col in inspect(type(obj)).columns if col.key not in excluded}


def user_data(obj, db=None):
    return fields(obj, {'hashed_password', 'face_embedding_hash', 'pending_face_hash'})


def course_data(obj, db=None):
    return fields(obj)


def session_data(obj, db=None):
    result = fields(obj, {'qr_code_secret'})
    if db is not None:
        course = db.get(Course, obj.course_id)
        if course:
            result.update(course_code=course.code, course_name=course.name)
            for key in ('venue_latitude', 'venue_longitude', 'venue_name', 'geofence_radius_meters', 'risk_threshold'):
                if result[key] is None:
                    result[key] = getattr(course, key)
    return result


def checkin_data(obj, db=None):
    result = fields(obj, {'face_embedding_hash'})
    if db is not None:
        student = db.get(User, obj.student_id)
        session = db.get(Session, obj.session_id)
        if student:
            result.update(student_name=student.full_name, student_email=student.email)
        if session:
            result['session_name'] = session.name
            course = db.get(Course, session.course_id)
            if course:
                result.update(course_id=course.id, course_code=course.code)
    return result


def device_data(obj, db=None):
    return fields(obj, {'public_key'})


def enrollment_data(obj, db=None):
    result = fields(obj)
    if db is not None:
        course = db.get(Course, obj.course_id)
        student = db.get(User, obj.student_id)
        if course:
            result.update(course_code=course.code, course_name=course.name, semester=course.semester)
        if student:
            result.update(student_name=student.full_name, student_email=student.email)
    return result


def page(items, total, limit, offset):
    return {'items': items, 'total': total, 'limit': limit, 'offset': offset}
