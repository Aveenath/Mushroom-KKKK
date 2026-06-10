import streamlit as st


def show():
    st.title("📜 Standard Operating Procedures (SOP)")
    st.write("Adhere to these clinical guidelines to ensure maximum yield and minimize contamination risks.")

    st.markdown("---")
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("🌡️ 1. Environmental Control")
        with st.expander("Temperature Management", expanded=True):
            st.info("**Target:** 24°C - 28°C")
            st.markdown("""
            - **Above 30°C:** Activate exhaust fans to 100% capacity.
            - **Below 22°C:** Reduce ventilation and monitor closely.
            - *Notes: Avoid sudden extreme temperature shocks to prevent pinning abortions.*
            """)

        with st.expander("Humidity & Moisture", expanded=True):
            st.info("**Target:** 80% - 90% RH")
            st.markdown("""
            - **Misting:** Spray fine water mist upwards into the air 2-3 times daily.
            - **Warning:** NEVER spray water directly onto the mushroom caps (causes bacterial dark blotch).
            - Keep the floor damp but avoid standing stagnant water puddles.
            """)

        with st.expander("CO2 & Air Exchange"):
            st.info("**Target:** < 1000 ppm")
            st.markdown("""
            - High CO2 leads to long stems and small underdeveloped caps.
            - Ensure cross-flow ventilation is active for at least 30 minutes every 4 hours.
            """)

    with col2:
        st.subheader("🛡️ 2. Disease & Contamination")
        with st.expander("Trichoderma (Green Mold) Protocol", expanded=True):
            st.error("**CRITICAL: Highly Contagious**")
            st.image("picture/Trichoderma.jpeg", use_container_width=True)
            st.markdown("""
            1. **Identification:** Fluffy white patches turning into forest-green powder.
            2. **Immediate Action:** Remove the contaminated bag without squeezing it.
            3. **Disposal:** Seal in a garbage bag and dispose of it far from the active farm.
            4. **Sanitization:** Spray 70% Isopropyl alcohol in the area where the bag sat.
            """)

        with st.expander("Neurospora (Orange Mold)"):
            st.warning("**Fast Spreading Spores**")
            st.image("picture/Neurospora.jpeg", use_container_width=True)
            st.markdown("""
            - Orange/Pink powdery mold spread quickly via airborne dust.
            - Carefully isolate out of the facility immediately.
            - Keep facility air-filters clean.
            """)

        with st.expander("General Pest Control"):
            st.image("picture/general_pest_control.jpeg", use_container_width=True)
            st.markdown("""
            - **Fungus Gnats & Flies:** Install yellow sticky insect traps near light panels.
            - Maintain strict hygiene. Ensure all workers deploy footbaths before entering.
            """)

    st.markdown("---")
    st.subheader("✂️ 3. Harvesting Guidelines")
    st.image("picture/Harvesting_Guidelines.jpeg", use_container_width=True)
    st.success("Optimal Harvesting Window: Just before the cap edges flatten or begin turning upwards.")
    st.markdown("""
    - **Technique:** Firmly hold the base of the mushroom cluster, gently twist, and pull. Do not cut with a knife as leaving stump remnants promotes rotting.
    - **Hygiene:** Handlers must sanitize hands with 70% alcohol or wear fresh gloves before contact.
    - **Post-Harvest:** Clean the opening of the block after harvest to encourage secondary fruiting flushes.
    """)
