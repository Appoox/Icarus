# articles/tasks.py
import os
import logging
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils.html import strip_tags
from google.cloud import texttospeech
from wagtail.documents import get_document_model
from wagtail.signals import page_published
from Icarus.settings.base import GOOGLE_CREDENTIALS_JSON

logger = logging.getLogger(__name__)

def generate_article_audio_task(article_id):
    from .models import Article
    from .signals import on_article_published  # Import the signal receiver to temporarily disconnect

    try:
        article = Article.objects.get(pk=article_id)
    except Article.DoesNotExist:
        logger.warning(f"Article with ID {article_id} does not exist.")
        return

    # 1. Extract clean text from Title and StreamField text blocks
    text_parts = [article.title]
    if article.body:
        for block in article.body:
            if block.block_type in ['paragraph', 'text', 'rich_text', 'heading', 'colored_heading', 'blockquote']:
                if block.block_type == 'blockquote':
                    block_text = str(block.value.get('text', ''))
                    attrib = block.value.get('attribute_name', '')
                    if attrib:
                        block_text += f" - {attrib}"
                else:
                    block_text = str(block.value)
                
                cleaned = strip_tags(block_text).strip()
                if cleaned:
                    text_parts.append(cleaned)

    full_text = " ".join(text_parts).strip()
    if not full_text:
        logger.info(f"Article {article.id} has no synthesizable text.")
        return

    # 2. Select appropriate Chirp 3 voice.
    has_malayalam = any('\u0d00' <= char <= '\u0d7f' for char in full_text)
    if has_malayalam:
        lang_code = "ml-IN"
        voice_name = "ml-IN-Chirp3-HD-Charon"  # Premium Generative Chirp3 voice
    else:
        lang_code = "en-US"
        voice_name = "en-US-Chirp3-HD-Charon"

    try:
        credentials_path = GOOGLE_CREDENTIALS_JSON
        
        if credentials_path and os.path.exists(credentials_path):
            client = texttospeech.TextToSpeechClient.from_service_account_json(credentials_path)
        else:
            client = texttospeech.TextToSpeechClient()

        # 3. SPLIT BY BYTES (Safely targeting 4500 bytes to leave a buffer under GCP's 5000-byte limit)
        chunks = []
        words = full_text.split()
        current_chunk = []
        current_byte_len = 0
        
        for word in words:
            # Measure byte-length of word in UTF-8
            word_byte_len = len(word.encode('utf-8'))
            
            # Plus 1 byte for the space used when joining words
            space_byte_len = 1 if current_chunk else 0
            
            if current_byte_len + word_byte_len + space_byte_len > 4500:
                # Store the full chunk and start a new one
                chunks.append(" ".join(current_chunk))
                current_chunk = [word]
                current_byte_len = word_byte_len
            else:
                current_chunk.append(word)
                current_byte_len += word_byte_len + space_byte_len
                
        if current_chunk:
            chunks.append(" ".join(current_chunk))

        combined_audio_bytes = bytearray()

        # 4. Request speech synthesis for each byte-safe chunk
        for chunk in chunks:
            synthesis_input = texttospeech.SynthesisInput(text=chunk)
            voice_params = texttospeech.VoiceSelectionParams(
                language_code=lang_code,
                name=voice_name
            )
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3
            )
            
            response = client.synthesize_speech(
                input=synthesis_input,
                voice=voice_params,
                audio_config=audio_config
            )
            combined_audio_bytes.extend(response.audio_content)

        if not combined_audio_bytes:
            logger.warning(f"No audio content returned from Google TTS API for article {article_id}.")
            return

        # 5. Save the generated audio as a Wagtail Document
        Document = get_document_model()
        
        if article.audio_file:
            old_doc = article.audio_file
            if old_doc.title.startswith("TTS Audio - "):
                old_doc.delete()

        audio_file_content = ContentFile(bytes(combined_audio_bytes), name=f"tts_article_{article.id}.mp3")
        doc = Document(
            title=f"TTS Audio - {article.title}",
            file=audio_file_content
        )
        doc.save()

        # 6. Bind document and publish changes securely
        article.audio_file = doc
        article.save()

        page_published.disconnect(on_article_published, sender=Article)
        try:
            revision = article.save_revision()
            revision.publish()
        finally:
            page_published.connect(on_article_published, sender=Article)

        logger.info(f"Successfully generated and published audio for Article {article.id}.")

    except Exception as e:
        logger.error(f"Failed to generate TTS audio for Article {article.id}: {str(e)}")