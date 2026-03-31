from langchain_huggingface import HuggingFaceEmbeddings

class EmbedderFactory:
    def __init__(self,embedder):
        self.embedder_type = embedder

    def create_embedder(self):
        print (f"Creating embedder of type: {self.embedder_type}")

        if self.embedder_type == "HuggingFace":
            embedder = HuggingFaceEmbeddings(model_name="all-mpnet-base-v2")
            return embedder
        else:
            raise ValueError(f"Unknown type: {self.embedder_type}")
        