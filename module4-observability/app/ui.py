import base64
from contextlib import contextmanager
from functools import lru_cache
from html import escape
from pathlib import Path

import streamlit as st


ASSETS = Path(__file__).with_name("assets")


@lru_cache(maxsize=1)
def stylesheet():
    font = base64.b64encode((ASSETS / "JetBrainsMono-Medium.woff2").read_bytes()).decode("ascii")
    return (ASSETS / "dashboard.css").read_text().replace("{{INTER_FONT}}", font)


def apply_theme():
    st.markdown("<style>" + stylesheet() + "</style>", unsafe_allow_html=True)


@contextmanager
def panel():
    with st.container(border=True):
        st.markdown('<span class="saiv-panel-anchor" aria-hidden="true"></span>', unsafe_allow_html=True)
        yield


def brand():
    st.markdown('''<div class="saiv-brand"><span class="saiv-mark" aria-hidden="true">s</span>
        <div><strong>SAIV</strong><span>Attendance</span></div></div>''', unsafe_allow_html=True)


def profile(name, role):
    initials = "".join(part[0] for part in name.split()[:2]).upper() or "S"
    st.markdown(f'''<div class="saiv-profile"><span class="saiv-avatar">{escape(initials)}</span>
        <div><strong>{escape(name)}</strong><span>{escape(role.capitalize())}</span></div></div>''', unsafe_allow_html=True)


def detail_header(title, subtitle, status):
    kind = status if status in {"active", "approved", "flagged", "appealed", "pending", "closed", "cancelled", "rejected", "scheduled"} else "scheduled"
    label = {"pending": "Re-check requested"}.get(status, status.capitalize())
    st.markdown(f'''<div class="saiv-detail-heading"><div><h3>{escape(str(title))}</h3>
        <p>{escape(str(subtitle))}</p></div><span class="saiv-status saiv-status-{kind}">{escape(label)}</span></div>''', unsafe_allow_html=True)


def metadata(items):
    fields = "".join(f'<div><span>{escape(str(label))}</span><strong>{escape(str(value if value is not None else "—"))}</strong></div>' for label, value in items)
    st.markdown('<div class="saiv-metadata">' + fields + '</div>', unsafe_allow_html=True)


def empty_state(title, message):
    st.markdown(f'''<div class="saiv-empty"><span aria-hidden="true">◇</span>
        <strong>{escape(title)}</strong><p>{escape(message)}</p></div>''', unsafe_allow_html=True)


def service_states(health):
    cards = []
    for key, label in (("api", "API"), ("database", "Database"), ("redis", "Redis")):
        state = str(health.get(key, "unknown"))
        kind = "healthy" if state == "healthy" else "unavailable" if state == "unavailable" else "unknown"
        cards.append(f'<div><span>{label}</span><strong><i class="saiv-dot saiv-dot-{kind}"></i>{escape(state.capitalize())}</strong></div>')
    st.markdown('<div class="saiv-services">' + "".join(cards) + '</div>', unsafe_allow_html=True)
