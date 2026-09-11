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

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

st.title("SAIV INSTRUCTOR DASHBOARD")

st.session_state.logged_in = True  # TODO - super important to remove this when mod 2 login feature up and running

if not st.session_state.logged_in:
    st.subheader("INSTRUCTOR LOGIN")

    email = st.text_input("email")
    password = st.text_input("password", type="password")

    if st.button("Login"):
        try:
            login_data = st.session_state.api.login(email, password)
            st.session_state.login_data = login_data
            st.session_state.logged_in  = True
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


if st.sidebar.button("LOGOUT"):
    st.session_state.logged_in  = False
    st.session_state.login_data = None
    st.session_state.api.token  = None
    st.rerun()


# pages
if   page == "OVERVIEW":
    st.header("OVERVIEW")

    try:

        # TODO - temp mock data , remember to remove once we have mod 2 up and running
        # TODO - this will be replaced with "overview = st.session_state.api.get_overview_stats()"

        overview = {
            "total_sessions":          12,
            "current_active_sessions": 2,
            "total_checkins":          482,
            "current_active_checkins": 453
        }

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric(
                "TOTAL SESSIONS",
                overview["total_sessions"]
            )

        with col2:
            st.metric(
                "CURRENT ACTIVE SESSIONS",
                overview["current_active_sessions"]
            )

        with col3:
            st.metric(
                "TOTAL CHECK-INS",
                overview["total_checkins"]
            )

        with col4:
            success_rate = (
                (overview["current_active_checkins"] / overview["total_checkins"]) * 100
            )

            st.metric(
                "SUCCESS RATE",
                f"{success_rate:.1f}%"
            )


        st.subheader("RECENT CHECK-INS")

        recent_checkins = [
            {
                "student": "Alice Tan",
                "session": "CS50 Lecture 1",
                "time":    "10:32 AM",
                "status":  "success"
            },


            {
                "student": "Benny Lim",
                "session": "CS50 Lecture 1",
                "time":    "10:30 AM",
                "status":  "success"
            },


            {
                "student": "James Tay",
                "session": "CS50 Lecture 1",
                "time":    "10:28 AM",
                "status":  "flagged"
            }
        ]

        st.dataframe(
            recent_checkins,
            use_container_width = True  # makes the table use the available width of the page
        )


        st.json(overview)
    except Exception as e:
        st.error(f"could not load overview statistics : {e}")


elif page == "SESSIONS":
    st.header("SESSIONS")

    # TODO - temporary mock data
    # TODO - replace with st.session_state.api.get_sessions() when mod 2 is running

    sessions = [
        {
            "id":          1,
            "name":       "CS50 Lecture 1",
            "course":     "CS50",
            "status":     "active",
            "start_time": "2026-03-01 10:00",
            "end_time":   "2026-03-01 12:00"
        },

        {
            "id":          2,
            "name":       "CS50 Seminar 1",
            "course":     "CS50",
            "status":     "inactive",
            "start_time": "2026-01-01 14:00",
            "end_time":   "2026-01-01 16:00"
        },

        {
            "id":          3,
            "name":       "CS88 Lecture 3",
            "course":     "CS88",
            "status":     "active",
            "start_time": "2026-03-01 10:00",
            "end_time":   "2026-03-01 12:00"
        }
    ]

    st.subheader("ALL SESSIONS")

    st.dataframe(
        sessions,
        use_container_width = True
    )

    session_options = {
        session["id"]: session["name"]
        for session in sessions  # For every session in the sessions list , take ID and name and put into DICT
    }

    selected_session_id = st.selectbox(
        "SELECT A SESSION",
        options = list(session_options.keys()),
        format_func = lambda session_id: session_options[session_id]
    )

    # this is just the entire DICT for the selected session
    selected_session = next(
        session for session in sessions
        if session["id"] == selected_session_id
    )

    st.subheader("SESSION DETAILS")

    col1 , col2 = st.columns(2)

    with col1:
        st.write(f"**SESSION ID:** {selected_session['id']}")
        st.write(f"**NAME:** {selected_session['name']}")
        st.write(f"**COURSE:** {selected_session['course']}")

    with col2:
        st.write(f"**STATUS:** {selected_session['status']}")
        st.write(f"**START:** {selected_session['start_time']}")
        st.write(f"**END:** {selected_session['end_time']}")


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
