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


if "api" not in st.session_state:  # asking if the current streamlit session already has an API client
    st.session_state.api = APIClient(BACKEND_URL)

if "logged in" not in st.session_state:
    st.session_state.logged_in = False

st.title("SAIV INSTRUCTOR DASHBOARD")

if not st.session_state.logged_in:
    st.subheader("INSTRUCTOR LOGIN")

    email = st.text_input("email")
    password = st.text_input("password", type="password")

    if st.button("Login"):
        try:
            st.session_state.api.login(email, password)
            st.session_state.login = True
            st.success("Login success!")
            st.rerun()
        except Exception as e:
            st.error(f"Login Failed : {e}")

    st.stop()


# navigation
st.sidebar.title("NAVIGATION")
page = st.sidebar.radio(  # this creates a group of selectable options
    "GO TO",
    [
        "OVERVIEW",
        "SESSIONS",
        "CHECK-INS",
        "AUDIT LOGS",
        "METRICS",
    ]
)


# pages
if   page == "OVERVIEW":
    st.header("overview")
    st.write("TODO - overview dashboard goes here")

elif page == "SESSIONS":
    st.header("sessions")
    st.write("TODO - session management goes here")

elif page == "CHECK-INS":
    st.header("check-ins")
    st.write("TODO - check-ins monitoring goes here")

elif page == "AUDIT LOGS":
    st.header("audit logs")
    st.write("TODO - audit logs goes here")

elif page == "METRICS":
    st.header("metrics")
    st.write("TODO - metrics and system health goes here")



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
