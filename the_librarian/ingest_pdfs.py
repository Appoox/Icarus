import os
from django.core.management.base import BaseCommand
from pdf2image import convert_from_path
from PIL import Image
import numpy as np

# Import the OCR services you copied over
from documents.services.ocr_processing import get_skew_corrected_image, process_image_for_ocr
from documents.services.yolo_segmentation import get_masks, custom_sort, general_model, image_model, configs
# Import your pgvector model
from documents.models import DocumentChunk
# You'll need a chunker and embedder (e.g., from LangChain or pure python)
# from my_embedder import get_embedding, chunk_text  

class Command(BaseCommand):
    help = 'Batch process PDFs: OCR, chunk, embed, and store in pgvector'

    def add_arguments(self, parser):
        parser.add_argument('pdf_directory', type=str, help='Path to the folder containing PDFs')

    def handle(self, *args, **kwargs):
        pdf_dir = kwargs['pdf_directory']
        
        for filename in os.listdir(pdf_dir):
            if not filename.endswith('.pdf'):
                continue
                
            file_path = os.path.join(pdf_dir, filename)
            self.stdout.write(f"Processing: {filename}")
            
            # 1. Convert PDF to images
            images = convert_from_path(file_path)
            
            for page_num, pil_image in enumerate(images):
                # 2. Skew Correction & YOLO Segmentation
                skew_corrected_np = get_skew_corrected_image(np.array(pil_image))
                skew_corrected_image = Image.fromarray(skew_corrected_np.astype("uint8"))
                
                yolo_bboxes_res = get_masks(skew_corrected_image, general_model, image_model, configs)
                if yolo_bboxes_res["status"] != 1:
                    continue
                    
                sorted_yolo_bboxes = custom_sort(yolo_bboxes_res["boxes1"])
                
                full_page_text = ""
                for bbox in sorted_yolo_bboxes:
                    # 3. OCR Extraction
                    yolo_cropped_np = skew_corrected_np[bbox[1]:bbox[3], bbox[0]:bbox[2]]
                    yolo_cropped_image = Image.fromarray(yolo_cropped_np.astype("uint8"))
                    
                    extracted_text = process_image_for_ocr(yolo_cropped_image) or ""
                    full_page_text += extracted_text + "\n"
                
                # 4. Chunking (e.g., using a text splitter)
                # chunks = chunk_text(full_page_text, chunk_size=500, overlap=50)
                chunks = [full_page_text] # Placeholder
                
                # 5. Embedding & Saving to pgvector
                for chunk in chunks:
                    if chunk.strip():
                        # embedding_vector = get_embedding(chunk)
                        embedding_vector = [0.0] * 1536 # Placeholder
                        
                        DocumentChunk.objects.create(
                            document_name=filename,
                            page_number=page_num + 1,
                            chunk_text=chunk,
                            embedding=embedding_vector
                        )
            
            self.stdout.write(self.style.SUCCESS(f"Successfully processed {filename}"))

