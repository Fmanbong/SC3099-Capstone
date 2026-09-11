"""
SAIV INSTRUCTOR DASHBOARD

Students must implement the instructor dashboard with session management,
attendance monitoring , and metrics visualisation
"""


import streamlit as st
import os
from api_client import APIClient

# page setup
st.set_page_config(
    page_title = "SAIV INSTRUCTOR DASHBOARD",
    page_icon  = "O..O", layout     = "wide",  # Layout == 'page elements use the entire screen width
)

BACKEND_URL    = os.getenv("BACKEND_URL",    "http://localhost:8000")  # environment vars
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")  # environment vars

api = APIClient(BACKEND_URL)

st.title("SAIV INSTRUCTOR DASHBOARD")
st.write("TODO - perhaps put a nice welcome message here")


# =============================================================================
# TODO: Implement the following pages/features
# =============================================================================

# -----------------------------------------------------------------------------
# Authentication
# -----------------------------------------------------------------------------
# - Login form for instructors
# - JWT token management
# - Session persistence

# -----------------------------------------------------------------------------
# Overview Page
# -----------------------------------------------------------------------------
# - Total sessions (active/inactive)
# - Total check-ins & success rate
# - Recent check-ins table
# - Check-ins by hour chart
# - Verification status pie chart

# -----------------------------------------------------------------------------
# Sessions Management
# -----------------------------------------------------------------------------
# - View all sessions with details
# - Create new sessions with geofence config
# - View check-ins per session
# - CSV export for gradebook

# -----------------------------------------------------------------------------
# Check-ins View
# -----------------------------------------------------------------------------
# - Filter by session, verification status
# - Real-time updates
# - CSV export for gradebook integration
# - Attendance data with all signals

# -----------------------------------------------------------------------------
# Audit Logs
# -----------------------------------------------------------------------------
# - Browse system events
# - Filter by event type, user, action
# - Color-coded by severity
# - Export audit trail

# -----------------------------------------------------------------------------
# Metrics Dashboard
# -----------------------------------------------------------------------------
# - API response times (p95 latency)
# - Request rates
# - Success rates
# - Risk score distribution
# - High-risk alerts
# - System health status

# =============================================================================
# CSV Export Format
# =============================================================================
# Required columns:
# - Check-in ID, Student ID, Session ID
# - Timestamp, Verification Status
# - Risk Score, Liveness Score, Face Match Score
# - GPS Coordinates (latitude, longitude)


# onncera start of project

st.subheader("SYSTEM SETUP")
st.write(f"BACKEND_API - '{BACKEND_URL}'")
st.write(f"PROMMETHEUS - '{PROMETHEUS_URL}'")
st.success("HEHE")

st.subheader("backend testing")
try:
    sessions = api.get_sessions()
    st.write(sessions)
except Exception as e:
    st.error(f"could not connect to the backend : {e}")
