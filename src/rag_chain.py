import json
import re
from langchain_core.prompts import PromptTemplate
from config import driver, embeddings, llm

def find_candidates(symptom: str, threshold: float = 0.78):
    """Esegue la ricerca vettoriale su Neo4j per trovare candidati farmacologici."""
    vector = embeddings.embed_query(symptom)
    
    query = """
    CALL db.index.vector.queryNodes('sintomo_vector', 15, $vec)
    YIELD node, score
    WHERE score >= $threshold
    MATCH (f:Farmaco)-[:CURA]->(node)
    RETURN distinct f.nome as farmaco, node.nome as match_sintomo, score
    ORDER BY score DESC
    """
    
    with driver.session() as session:
        result = session.run(query, vec=vector, threshold=threshold)
        
        candidates = []
        seen = set()
        for r in result:
            if r["farmaco"] not in seen:
                candidates.append(r.data())
                seen.add(r["farmaco"])
                if len(candidates) >= 5: break
                
    return candidates

def get_all_indications(drug_names: list):
    """Helper: Recupera le indicazioni ufficiali dal grafo per la validazione."""
    query = """
    MATCH (f:Farmaco)
    WHERE f.nome IN $drug_names
    OPTIONAL MATCH (f)-[:CURA]->(s:Sintomo)
    RETURN f.nome as farmaco, collect(s.nome) as indicazioni
    """
    with driver.session() as session:
        result = session.run(query, drug_names=drug_names)
        return {r["farmaco"]: r["indicazioni"] for r in result}

def verify_clinical_relevance(symptom: str, candidates: list):
    """Agente LLM: Filtra i candidati basandosi sulle indicazioni ufficiali (Ground Truth)."""
    if not candidates: return []
    
    candidate_names = [c['farmaco'] for c in candidates]
    indications_map = get_all_indications(candidate_names)
    
    check_list_str = ""
    for c in candidates:
        fname = c['farmaco']
        real_indications = indications_map.get(fname, [])
        indications_str = ", ".join(real_indications) if real_indications else "Nessuna indicazione"
        check_list_str += f"- FARMACO: {fname} (Match: '{c['match_sintomo']}')\n  INDICAZIONI UFFICIALI: [{indications_str}]\n"

    prompt = PromptTemplate.from_template("""
    Sei un Supervisore Clinico. Sto cercando un farmaco per il sintomo: "{symptom}".
    Ecco i candidati e le loro INDICAZIONI UFFICIALI:
    {check_list}
    
    COMPITO:
    Analizza le "INDICAZIONI UFFICIALI". 
    Se il farmaco è appropriato per il sintomo, includilo nella lista.
    IMPORTANTE: Restituisci il nome del farmaco ESATTAMENTE come scritto nella lista candidati.
    
    Rispondi ESCLUSIVAMENTE con una lista JSON di stringhe.
    Esempio: ["NomeFarmaco1", "NomeFarmaco2"]
    """)
    
    try:
        res = llm.invoke(prompt.format(symptom=symptom, check_list=check_list_str)).content
        match = re.search(r'\[.*\]', res, re.DOTALL)
        if match:
            valid_names_raw = json.loads(match.group())
            valid_names_norm = [n.strip().lower() for n in valid_names_raw]
            
            clean_candidates = []
            for c in candidates:
                db_name_norm = c['farmaco'].strip().lower()
                # Fuzzy matching logica
                if db_name_norm in valid_names_norm:
                    clean_candidates.append(c)
                    continue
                for valid_n in valid_names_norm:
                    if valid_n in db_name_norm or db_name_norm in valid_n:
                        clean_candidates.append(c)
                        break
            
            if not clean_candidates and candidates:
                return candidates 
            return clean_candidates
        return candidates
    except Exception:
        return candidates

def get_constraints(drug_name: str):
    """Recupera controindicazioni e interazioni dal Grafo."""
    query = """
    MATCH (f:Farmaco {nome: $name})
    OPTIONAL MATCH (f)-[r1:VIETATO_PER]->(c:Condizione)
    WITH f, collect({cond: c.nome, motivo: r1.motivo}) as contraind
    OPTIONAL MATCH (f)-[r2:INTERAGISCE_CON]->(sost:Sostanza)
    WITH f, contraind, collect({sost: sost.nome, eff: r2.effetto, grav: r2.gravità}) as interactions
    RETURN contraind, interactions
    """
    with driver.session() as session:
        result = session.run(query, name=drug_name).single()
        if result:
            return {"contraind": result["contraind"], "interactions": result["interactions"]}
        return None

def evaluate_safety(drug: str, constraints: dict, profile: dict):
    """Agente LLM: Ragiona sulla sicurezza incrociando profilo paziente e vincoli grafo."""
    prompt = PromptTemplate.from_template("""
    Ruolo: Supervisore Clinico AI.
    
    PAZIENTE:
    - Età: {age}
    - Patologie: {conditions}
    - Farmaci attuali: {meds}
    
    FARMACO '{drug}' - REGOLE DI SICUREZZA:
    - Controindicazioni: {contra}
    - Interazioni note: {inter}
    
    TASK DI RAGIONAMENTO:
    1. Confronta l'età del paziente con eventuali limiti. Sii rigoroso (es. 12 < 15 -> Vietato).
    2. Incrocia i farmaci attuali con la lista delle interazioni.
    3. Se trovi un conflitto GRAVE, lo status è RESPINTO.
    
    OUTPUT JSON:
    {{
        "status": "RESPINTO" | "ATTENZIONE" | "APPROVATO",
        "short_reason": "Motivazione sintetica (max 10 parole)",
        "detailed_reason": "Spiegazione logica dettagliata."
    }}
    """)
    
    res = llm.invoke(prompt.format(
        age=profile.get("age", "N/D"),
        conditions=profile.get("conditions", []),
        meds=profile.get("current_meds", []),
        drug=drug, 
        contra=json.dumps(constraints["contraind"], ensure_ascii=False), 
        inter=json.dumps(constraints["interactions"], ensure_ascii=False)
    )).content
    
    try:
        match = re.search(r'\{.*\}', res, re.DOTALL)
        if match: return json.loads(match.group())
        else: raise ValueError("No JSON found")
    except Exception:
        return {"status": "ATTENZIONE", "short_reason": "Errore Tecnico", "detailed_reason": res}