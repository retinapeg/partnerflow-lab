"""PartnerFlow Lab dashboard.

Run it on its own:   streamlit run dashboard.py --server.port 8510
(The API must be running too. `python run.py` starts both.)

Streamlit re-runs this whole file from top to bottom every time you click
something. Each page lives in its own file in the views/ folder.
"""

import streamlit as st

from partnerflow import config
from views import brokers, common, data_science, faults, investigation, overview, playground, release

PAGES = {
    "Overview": overview.render,
    "API playground": playground.render,
    "Fault injection": faults.render,
    "Release simulation": release.render,
    "Incident investigation": investigation.render,
    "Data science": data_science.render,
    "Broker monitoring": brokers.render,
}

st.set_page_config(page_title="PartnerFlow Lab", page_icon=":material/lab_research:", layout="wide")

# The banner is on every page.
st.warning(config.BANNER, icon=":material/info:")

# The dashboard is only a client of the API. Without the API there is nothing to show.
if not common.api_is_up():
    st.title("PartnerFlow Lab")
    st.error(f"The API is not answering at {config.api_url()}.")
    st.write("Start everything with one command from the project folder:")
    st.code("./start.sh", language="bash")
    st.button("Try again")
    st.stop()

lab = common.get_json("/lab/config")

with st.sidebar:
    st.title("PartnerFlow Lab")
    page = st.radio("Page", list(PAGES), key="page", label_visibility="collapsed")

    st.divider()
    st.caption("LAB STATE")
    st.write(f"API release: **{lab['version']}**")
    active = [config.FAULT_INFO[name]["label"] for name, fault in lab["faults"].items()
              if fault["enabled"]]
    if active:
        st.write("Active faults:")
        for label in active:
            st.write(f"- {label}")
    else:
        st.write("Active faults: none")

    st.divider()
    st.link_button("Open API docs (/docs)", config.api_url() + "/docs")
    with st.popover("Reset the lab"):
        st.write("Deletes every recorded request, customer and application, "
                 "and restores release v1.0.0 with no faults.")
        if st.button("Reset everything", type="primary"):
            common.call_api("POST", "/lab/reset")
            st.session_state.pop("playground_result", None)
            st.rerun()

PAGES[page](lab)
