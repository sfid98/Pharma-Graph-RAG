import streamlit as st
from rag_chain import find_candidates, verify_clinical_relevance, get_constraints, evaluate_safety

# --- CONFIGURAZIONE UI ---
st.set_page_config(page_title="PharmaGraph AI Assistant", layout="wide", page_icon="🩺")

# Gestione Stato per Pre-compilazione Scenari
if 'form_age' not in st.session_state: st.session_state['form_age'] = 30
if 'form_symptoms' not in st.session_state: st.session_state['form_symptoms'] = ""
if 'form_conds' not in st.session_state: st.session_state['form_conds'] = ""
if 'form_meds' not in st.session_state: st.session_state['form_meds'] = ""

def set_scenario():
    sc = st.session_state.scenario_selector
    if sc == "Caso A: Bambino 12 anni":
        st.session_state['form_age'] = 12
        st.session_state['form_symptoms'] = "Febbre alta, Dolori muscolari"
        st.session_state['form_conds'] = ""
        st.session_state['form_meds'] = ""
    elif sc == "Caso B: Anziana con Warfarin":
        st.session_state['form_age'] = 75
        st.session_state['form_symptoms'] = "Forte mal di testa"
        st.session_state['form_conds'] = "Fibrillazione atriale"
        st.session_state['form_meds'] = "Warfarin"
    elif sc == "Caso C: Paziente Sano":
        st.session_state['form_age'] = 40
        st.session_state['form_symptoms'] = "Torcicollo, Dolori articolari"
        st.session_state['form_conds'] = ""
        st.session_state['form_meds'] = ""

# --- SIDEBAR ---
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3063/3063176.png", width=80)    
    st.title("PharmaGraph AI")
    st.markdown("---")
    st.selectbox(
        "Carica Scenario Demo:", 
        ["Seleziona...", "Caso A: Bambino 12 anni", "Caso B: Anziana con Warfarin", "Caso C: Paziente Sano"],
        key="scenario_selector",
        on_change=set_scenario
    )

st.header("🏥 Prescrizioni & Sicurezza (Doctor Interface)")

# --- FORM DI INPUT STRUTTURATO ---
with st.container(border=True):
    st.subheader("📋 Dati Paziente & Richiesta")
    
    col1, col2 = st.columns(2)
    with col1:
        age_input = st.number_input("Età Paziente", min_value=0, max_value=120, value=st.session_state['form_age'])
        meds_input = st.text_input("Farmaci in uso (separati da virgola)", value=st.session_state['form_meds'], placeholder="Es. Warfarin, Metformina")
    with col2:
        conds_input = st.text_input("Patologie Pregresse (separate da virgola)", value=st.session_state['form_conds'], placeholder="Es. Diabete, Ipertensione")
        symptoms_input = st.text_input("Sintomo da trattare (Target)", value=st.session_state['form_symptoms'], placeholder="Es. Mal di testa, Febbre")

    analyze_btn = st.button("🔍 Analizza e Suggerisci Terapia", type="primary", use_container_width=True)

# --- LOGICA DI ELABORAZIONE ---
if analyze_btn:
    if not symptoms_input:
        st.error("Inserire almeno un sintomo da trattare.")
        st.stop()

    # 1. Costruzione Profilo
    profile = {
        "age": age_input,
        "conditions": [c.strip() for c in conds_input.split(",") if c.strip()],
        "current_meds": [m.strip() for m in meds_input.split(",") if m.strip()],
        "symptoms": [s.strip() for s in symptoms_input.split(",") if s.strip()]
    }

    with st.expander("Dati Paziente Elaborati", expanded=False):
        st.json(profile)

    # 2. Ricerca e Validazione
    sintomo_target = profile['symptoms'][0]
    
    with st.spinner(f"Ricerca farmaci per '{sintomo_target}'..."):
        raw_candidates = find_candidates(sintomo_target)
        
        if not raw_candidates:
            st.warning(f"Nessun farmaco trovato per: {sintomo_target}")
            st.stop()
            
        candidates = verify_clinical_relevance(sintomo_target, raw_candidates)

    if not candidates:
        st.error(f"Nessun farmaco clinicamente appropriato trovato per '{sintomo_target}'.")
        st.stop()

    # 3. Report Sicurezza
    st.subheader(f"💊 Opzioni Terapeutiche per: {sintomo_target}")
    progress_bar = st.progress(0)
    
    for i, cand in enumerate(candidates):
        drug_name = cand["farmaco"]
        constraints = get_constraints(drug_name)
        eval_result = evaluate_safety(drug_name, constraints, profile)
        
        # Logica Colori
        status = eval_result["status"]
        if status == "RESPINTO":
            color = "red"
            icon = "⛔"
        elif status == "ATTENZIONE":
            color = "orange"
            icon = "⚠️"
        else:
            color = "green"
            icon = "✅"

        # Rendering Card
        with st.container():
            if color == "red":
                st.error(f"**{icon} {drug_name}** — {status}")
            elif color == "orange":
                st.warning(f"**{icon} {drug_name}** — {status}")
            else:
                st.success(f"**{icon} {drug_name}** — {status}")
            
            c1, c2 = st.columns([0.7, 0.3])
            with c1:
                st.markdown(f"**Motivazione:** {eval_result['short_reason']}")
                with st.expander("Dettagli ragionamento"):
                    st.write(eval_result["detailed_reason"])
            with c2:
                # Evidence
                if constraints["contraind"]:
                    st.markdown(f"🚫 **{len(constraints['contraind'])}** Controindicazioni")
                if constraints["interactions"]:
                    st.markdown(f"⚡ **{len(constraints['interactions'])}** Interazioni")
                    
        progress_bar.progress((i + 1) / len(candidates))
    
    progress_bar.empty()