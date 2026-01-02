import json
import os
import time
from neo4j import GraphDatabase
from config import get_embeddings
# --- CONFIGURAZIONE ---
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "password_segreta"


# Lista dei file da caricare
JSON_FILES = ["tachiverde_data.json", "aspirina_data.json", "tachipirina_data.json", "tachipirina_supposta.json", "ramipril_data.json", "levopraid.json"]

class MedicalGraphIngestor:
    def __init__(self, uri, user, password):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.embeddings = get_embeddings()

    def close(self):
        self.driver.close()

    def clear_database(self):
        """Pulisce il DB una volta sola all'inizio"""
        print("\n🧹 PULIZIA TOTALE DATABASE...")
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
            # Droppa indici vecchi per ricrearli puliti
            for index in ["medical_text_index", "sintomo_vector", "condizione_vector"]:
                try:
                    session.run(f"DROP INDEX {index} IF EXISTS")
                except:
                    pass
        print("✅ Database vuoto.")

    def create_indexes(self):
        """Crea gli indici necessari gestendo errori se esistono già"""
        print("⚙️ Creazione Indici Vettoriali e Vincoli...")
        with self.driver.session() as session:
            # 1. VINCOLI (Constraints) - Qui IF NOT EXISTS è supportato e sicuro
            # Nota: se anche qui ti da errore, rimuovi IF NOT EXISTS e usa try-except come sotto
            try:
                session.run("CREATE CONSTRAINT FOR (f:Farmaco) REQUIRE f.nome IS UNIQUE IF NOT EXISTS")
                session.run("CREATE CONSTRAINT FOR (pa:PrincipioAttivo) REQUIRE pa.nome IS UNIQUE IF NOT EXISTS")
                session.run("CREATE CONSTRAINT FOR (s:Sintomo) REQUIRE s.nome IS UNIQUE IF NOT EXISTS")
                session.run("CREATE CONSTRAINT FOR (c:Condizione) REQUIRE c.nome IS UNIQUE IF NOT EXISTS")
            except Exception as e:
                print(f"   ⚠️ Warning creazione vincoli (potrebbero esistere già): {e}")

            # 2. INDICI VETTORIALI - RIMOSSO 'IF NOT EXISTS' per compatibilità
            
            # Indice A: medical_text_index
            try:
                session.run("""
                    CREATE VECTOR INDEX medical_text_index
                    FOR (s:SezioneTestuale) ON (s.embedding)
                    OPTIONS {indexConfig: {`vector.dimensions`: 768, `vector.similarity_function`: 'cosine'}}
                """)
                print("   -> Creato indice: medical_text_index")
            except Exception as e:
                # Ignoriamo l'errore solo se dice che esiste già
                if "already exists" in str(e) or "EquivalentSchemaRuleAlreadyExists" in str(e):
                    pass 
                else:
                    print(f"   ⚠️ Errore indice medical_text_index: {e}")

            # Indice B: sintomo_vector
            try:
                session.run("""
                    CREATE VECTOR INDEX sintomo_vector
                    FOR (s:Sintomo) ON (s.embedding)
                    OPTIONS {indexConfig: {`vector.dimensions`: 768, `vector.similarity_function`: 'cosine'}}
                """)
                print("   -> Creato indice: sintomo_vector")
            except Exception as e:
                if "already exists" in str(e) or "EquivalentSchemaRuleAlreadyExists" in str(e):
                    pass
                else:
                    print(f"   ⚠️ Errore indice sintomo_vector: {e}")

            # Indice C: condizione_vector
            try:
                session.run("""
                    CREATE VECTOR INDEX condizione_vector
                    FOR (c:Condizione) ON (c.embedding)
                    OPTIONS {indexConfig: {`vector.dimensions`: 768, `vector.similarity_function`: 'cosine'}}
                """)
                print("   -> Creato indice: condizione_vector")
            except Exception as e:
                if "already exists" in str(e) or "EquivalentSchemaRuleAlreadyExists" in str(e):
                    pass
                else:
                    print(f"   ⚠️ Errore indice condizione_vector: {e}")

        time.sleep(2) # Attesa propagazione
        print("✅ Indici pronti.")

    def _prepare_semantic_list(self, string_list):
        """Genera embedding per liste di stringhe"""
        result = []
        if not string_list: return result
        
        # Deduplica la lista per risparmiare tempo
        unique_list = list(set(string_list))
        
        for text in unique_list:
            # Skip stringhe vuote
            if not text or len(text) < 2: continue
            
            vector = self.embeddings.embed_query(text)
            result.append({"nome": text, "embedding": vector})
        return result

    def ingest_single_file(self, filename):
        """Carica un singolo file JSON nel grafo esistente"""
        print(f"\n📂 Elaborazione file: {filename}...")
        
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except FileNotFoundError:
            print(f"❌ File {filename} non trovato. Salto.")
            return

        farmaco_nome = data['farmaco']['nome']
        print(f"   💊 Farmaco rilevato: {farmaco_nome}")

        # --- PREPARAZIONE DATI VETTORIALI (PYTHON SIDE) ---
        print("   🧠 Generazione embeddings...")
        sintomi_vec = self._prepare_semantic_list(data['grafo_strutturato']['indicazioni_terapeutiche'])
        
        condizioni_raw = [i['condizione'] for i in data['grafo_strutturato'].get('controindicazioni_assolute', [])]
        condizioni_raw += data['grafo_strutturato'].get('avvertenze_precauzioni', [])
        condizioni_vec = self._prepare_semantic_list(condizioni_raw)

        # --- CARICAMENTO IN NEO4J ---
        print("   🏗️ Caricamento in Neo4j...")
        with self.driver.session() as session:
            # 1. Nodo Farmaco Base
            session.run("""
                MERGE (f:Farmaco {nome: $nome})
                SET f.principio = $principio, 
                    f.gruppo = $gruppo
            """, nome=farmaco_nome, 
                 principio=data['farmaco']['principio_attivo'],
                 gruppo=data['farmaco'].get('gruppo_terapeutico', ''))

            # 2. Sintomi (Merge semantico)
            session.run("""
                MATCH (f:Farmaco {nome: $f_nome})
                UNWIND $items AS item
                MERGE (s:Sintomo {nome: item.nome})
                ON CREATE SET s.embedding = item.embedding
                MERGE (f)-[:CURA]->(s)
            """, f_nome=farmaco_nome, items=sintomi_vec)

            # 3. Condizioni (Merge semantico)
            session.run("""
                UNWIND $items AS item
                MERGE (c:Condizione {nome: item.nome})
                ON CREATE SET c.embedding = item.embedding
            """, items=condizioni_vec)

            # 4. Relazioni Logiche (Controindicazioni)
            if 'controindicazioni_assolute' in data['grafo_strutturato']:
                session.run("""
                    MATCH (f:Farmaco {nome: $f_nome})
                    UNWIND $items AS item
                    MATCH (c:Condizione {nome: item.condizione})
                    MERGE (f)-[:VIETATO_PER {motivo: item.motivo, gravità: 'ASSOLUTA'}]->(c)
                """, f_nome=farmaco_nome, items=data['grafo_strutturato']['controindicazioni_assolute'])

            # 5. Interazioni (Farmaco -> Sostanza)
            if 'interazioni_farmacologiche' in data['grafo_strutturato']:
                 session.run("""
                    MATCH (f:Farmaco {nome: $f_nome})
                    UNWIND $items AS item
                    MERGE (sost:Sostanza {nome: item.sostanza})
                    MERGE (f)-[:INTERAGISCE_CON {
                        effetto: item.effetto,
                        gravità: item.gravità
                    }]->(sost)
                """, f_nome=farmaco_nome, items=data['grafo_strutturato']['interazioni_farmacologiche'])

            # 6. Sezioni Testuali (Vector RAG)
            if 'sezioni_testuali' in data:
                for sec in data['sezioni_testuali']:
                    vector = self.embeddings.embed_query(sec['testo'])
                    session.run("""
                        MATCH (f:Farmaco {nome: $f_nome})
                        CREATE (s:SezioneTestuale {
                            categoria: $cat,
                            testo: $txt,
                            embedding: $vec
                        })
                        CREATE (f)-[:HA_DETTAGLIO_TESTUALE]->(s)
                    """, f_nome=farmaco_nome, cat=sec['categoria'], txt=sec['testo'], vec=vector)

        print(f"   ✅ {farmaco_nome} caricato con successo.")

# --- MAIN ---
if __name__ == "__main__":
    ingestor = MedicalGraphIngestor(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    
    try:
        # 1. Pulisci tutto UNA SOLA VOLTA
        ingestor.clear_database()
        ingestor.create_indexes()
        
        # 2. Cicla su tutti i file e caricali uno dopo l'altro
        for json_file in JSON_FILES:
            ingestor.ingest_single_file('../data/' + json_file)
            
        print("\n🎉 TUTTI I FARMACI CARICATI! Il database ora contiene sia Tachiverde che Aspirina.")
        
    finally:
        ingestor.close()
 