import os
from dotenv import load_dotenv
from neo4j import GraphDatabase
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_groq import ChatGroq

# Carica variabili d'ambiente
load_dotenv()

# --- CONSTANTS ---
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_AUTH = (os.getenv("NEO4J_USERNAME", "neo4j"), os.getenv("NEO4J_PASSWORD", "password"))

EMBEDDING_MODEL_NAME = "models/text-embedding-004"
LLM_MODEL_NAME = "llama-3.3-70b-versatile"

# --- FACTORY FUNCTIONS ---

def get_db_driver():
    return GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)

def get_embeddings():
    return GoogleGenerativeAIEmbeddings(
        model=EMBEDDING_MODEL_NAME,
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        task_type="retrieval_document"
    )

def get_llm():
    return ChatGroq(
        temperature=0, 
        model_name=LLM_MODEL_NAME,
        api_key=os.getenv("GROQ_API_KEY")
    )

# Singleton instances per l'uso nei moduli
driver = get_db_driver()
embeddings = get_embeddings()
llm = get_llm()