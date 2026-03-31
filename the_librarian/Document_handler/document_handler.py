from langchain_community.document_loaders import PDFPlumberLoader
from langchain_experimental.text_splitter import SemanticChunker
from pathlib import Path

class PDFLoader:
    """Handles loading PDFs"""

    def __init__(self, pdf_directory):
        self.pdf_directory = pdf_directory

    def load_all_pdfs(self):
        """Load all PDFs from the directory"""
        print("\nLoading PDFs...")
        
        pdf_directory = Path(self.pdf_directory) / "Documents"
        pdf_files = list(pdf_directory.glob("*.pdf"))
        
        if not pdf_files:
            print("No PDF files found!")
            exit(1)
        
        #load each pdfs
        all_docs = []
        for pdf_file in pdf_files:
            print(f" Loading: {pdf_file.name}")
            loader = PDFPlumberLoader(str(pdf_file))
            docs = loader.load()
            for doc in docs:
                doc.metadata['source_file'] = pdf_file.name
            all_docs.extend(docs)

        print(f" Loaded  {len(all_docs)} pages")
        return all_docs

class Chunker:
    """Turnes PDFs into vector chunks"""

    def __init__(self, embedder):
        self.text_splitter = SemanticChunker(embedder)
    
    def split_documents(self, documents):
        print("Splitting documents into chunks...")
        chunks = self.text_splitter.split_documents(documents)
        print(f" Created {len(chunks)} chunks")
        return chunks