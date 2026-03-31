#load system libraries
import os
from dotenv import load_dotenv
from pathlib import Path
import sys

#load local files
from Document_handler import document_handler
from Prompt import prompt
from Bot import interface
from Database import db_factory
from Embedding import embedding_factory
from LargeLanguageModel import llm_factory


def main():

    # dname = Path(__file__).parent
    # os.chdir(dname)
    # sys.path.append(dname)

    # load_dotenv("database.env") 

    # llm_type = os.getenv("LLM_TYPE")
    embedder_type = os.getenv("EMBEDDER_TYPE")
    # db_type = os.getenv("DB_TYPE")
    collection_name = os.getenv("COLLECTION_NAME")


    # llm_fact = llm_factory.LLMFactory(llm_type)
    # llm = llm_fact.create_llm()

    embeddr_fact = embedding_factory.EmbedderFactory(embedder_type)
    embedder = embeddr_fact.create_embedder()
    
    # db_fact = db_factory.DatabaseFactory(db_type)
    # db =db_fact.create_db_manager(
    #     embedder=embedder,
    #     collection_name=collection_name
    # )

    chunks = document_handler.Chunker(embedder)
    documents = chunks.split_documents(all_docs)

    collection_exists = db.collection_exists()

    # if collection_exists is True:
    #     vector_store = db.load_existing_collection()

    # else: 
    #     pdf_loader = document_handler.PDFLoader(dname)
    #     all_docs = pdf_loader.load_all_pdfs()

    #     chunks = document_handler.Chunker(embedder)
    #     documents = chunks.split_documents(all_docs)

    #     vector_store = db.create_collection(documents) 
    
    # chain =  prompt.Prompt(vector_store,llm)
    
    # chatbot = interface.BotInterface(chain)
    # chatbot.run()


if __name__ == "__main__":
    main()

