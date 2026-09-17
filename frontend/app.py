"""Streamlit UI for the restaurant recommendation API (Phase 5).

Talks only to POST /recommendations (Phase 4) over HTTP — the sole local
data access here is the static CUISINE_OPTIONS list below, kept static
rather than reading data/restaurants.parquet directly, so the frontend
stays decoupled from the backend's data layer per architecture.md's
layering (Frontend -> API Layer only).
"""

import os

import requests
import streamlit as st

API_BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000")

CUISINE_OPTIONS = [
    "North Indian", "South Indian", "Chinese", "Continental", "Italian",
    "Cafe", "Desserts", "Biryani", "Fast Food", "Mughlai", "Thai",
    "Mexican", "American", "Arabian", "Bakery", "Beverages", "BBQ",
    "Seafood", "Street Food", "Mediterranean", "Kerala", "Bengali",
    "Rajasthani", "Andhra", "Punjabi", "Healthy Food", "Pizza", "Burger",
]

BUDGET_OPTIONS = {"Any": None, "Low": "low", "Medium": "medium", "High": "high"}


def render_stars(rating: float) -> str:
    full = max(0, min(5, round(rating)))
    return "★" * full + "☆" * (5 - full)


st.set_page_config(page_title="Restaurant Recommender", page_icon="🍽️")
st.title("🍽️ Restaurant Recommender")
st.caption("Tell us what you're in the mood for and we'll find the best matches.")

with st.form("preferences_form"):
    col1, col2 = st.columns(2)
    with col1:
        location = st.text_input("Location", placeholder="e.g. BTM, Koramangala, Indiranagar")
        budget_label = st.selectbox("Budget", list(BUDGET_OPTIONS.keys()))
    with col2:
        cuisine = st.multiselect("Cuisine (optional)", CUISINE_OPTIONS)
        min_rating = st.slider("Minimum rating", 0.0, 5.0, 3.5, 0.5)

    preferences_text = st.text_input(
        "Other preferences (optional, comma-separated)",
        placeholder="e.g. family-friendly, good ambience, quick service",
    )

    submitted = st.form_submit_button("Find restaurants")

if submitted:
    if not location.strip():
        st.error("Please enter a location.")
    else:
        preferences = [p.strip() for p in preferences_text.split(",") if p.strip()]
        payload = {
            "location": location.strip(),
            "budget": BUDGET_OPTIONS[budget_label],
            "cuisine": cuisine,
            "min_rating": min_rating,
            "preferences": preferences,
        }

        data = None
        try:
            with st.spinner("Finding the best restaurants for you..."):
                response = requests.post(f"{API_BASE_URL}/recommendations", json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.HTTPError:
            st.error(f"The recommendation service rejected the request: {response.text}")
        except requests.exceptions.RequestException as exc:
            st.error(f"Could not reach the recommendation service: {exc}")

        if data is not None:
            recommendations = data.get("recommendations", [])
            summary = data.get("summary", "")
            source = data.get("source", "fallback")

            if not recommendations:
                st.info(summary or "No restaurants matched your filters. Try relaxing them.")
            else:
                if source == "ai":
                    st.success(f"✨ AI-recommended — {summary}")
                else:
                    st.warning(
                        f"⚠️ AI ranking unavailable — showing restaurants sorted by rating instead. {summary}"
                    )

                for item in recommendations:
                    with st.container(border=True):
                        st.subheader(item["name"])
                        st.write(f"**Cuisine:** {item['cuisine']}")
                        st.write(f"**Rating:** {render_stars(item['rating'])} ({item['rating']:.1f})")
                        st.write(f"**Estimated cost:** {item['estimated_cost']}")
                        st.write(item["explanation"])
